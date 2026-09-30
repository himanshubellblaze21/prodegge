"""
Evaluation Lambda — Prodigee Finance Audio PD
Three call types, each scored against the rubric of the current scorecard
version (see scorecards/registry.json). v1 = client V2.1 Simple:

BCM_PHYSICAL_PD : 35 items, 7 sections (A–G), 100 points
BM_AUDIO_FI     : 25 items, 7 sections (A–G), 100 points
RCM_AUDIO_PD    : 35 items, 9 sections (A–I), 100 points

Scoring: Yes=full · Half=half · No=0
MUST items (★): any Half or No → recording NOT ACCEPTED regardless of score
Pass: 75+ with all MUST items full
Excellent: 90+
"""
import json, boto3, os, re, time
from botocore.config import Config
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Tuple

try:
    import scorecards
except ImportError:  # running from the repo (tests / tools), not the Lambda zip
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    import scorecards

# ── AWS clients ──────────────────────────────────────────────────────────────
# Throttling is the normal failure mode here: one evaluation fires 12-15
# Bedrock calls, and several evaluations often run at once. When retries ran
# out, whole sections failed and every criterion in them defaulted to "No",
# which used to be published as a 0/100 verdict. Ride out throttling rather
# than producing a wrong answer — the Lambda has a 900s budget and a normal
# evaluation uses 30-80s of it.
bedrock_runtime = boto3.client(
    'bedrock-runtime',
    region_name=os.environ.get('AWS_REGION', 'ap-south-1'),
    config=Config(read_timeout=300, connect_timeout=60,
                  retries={'max_attempts': 10, 'mode': 'adaptive'})
)
s3_client = boto3.client('s3')
dynamodb  = boto3.resource('dynamodb')

TRANSCRIPTS_BUCKET = os.environ['TRANSCRIPTS_BUCKET']
REPORTS_BUCKET     = os.environ['REPORTS_BUCKET']
DYNAMODB_TABLE     = os.environ['DYNAMODB_TABLE']
BEDROCK_MODEL_ID   = os.environ.get('BEDROCK_MODEL_ID', 'apac.amazon.nova-pro-v1:0')

# Shortest transcript worth evaluating. The shortest legitimate real recording
# seen so far is a ~2.5 min BM call at ~1,400 chars, so this only catches
# genuinely empty/silent/failed audio.
MIN_TRANSCRIPT_CHARS = int(os.environ.get('MIN_TRANSCRIPT_CHARS', '200'))

# Shortest quote accepted as evidence for a score (see verify_evidence).
MIN_EVIDENCE_CHARS  = 15
MIN_EVIDENCE_TOKENS = 4

# Above this share of criteria defaulting to "No" because the evaluator could
# not be reached, the run is treated as a failure rather than a 0/100 verdict.
MAX_DEFAULTED_SHARE = float(os.environ.get('MAX_DEFAULTED_SHARE', '0.25'))

# Seconds to wait after a throttled Bedrock call before retrying. This
# account's Nova Pro quota is small — throttling peaked at 875 rejections in a
# five-minute window — so the waits are long enough to clear the rate window
# instead of spending every retry inside it.
THROTTLE_BACKOFF = [5, 15, 30, 45]

# Pause between consecutive Bedrock calls in one evaluation. An evaluation
# makes 12-15 calls; firing them back to back is what tripped the quota.
INTER_CALL_DELAY = float(os.environ.get('INTER_CALL_DELAY', '1.5'))

# Nova Pro's full response budget. The element-by-element scoring output is
# several times longer than a bare mark, and a truncated response is a failed
# section.
MAX_OUTPUT_TOKENS = int(os.environ.get('MAX_OUTPUT_TOKENS', '10000'))

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal): return float(obj)
        return super().default(obj)

# ── Scorecard (rubric) ─────────────────────────────────────────────────────────
# Criteria, points, MUST flags, sections and the per-criterion scoring rules
# come from the versioned scorecard registry (scorecards/ at the repo root,
# bundled into the deployment zip). Every evaluation records the version it
# was scored on, so the Excel generator writes it into the matching template.
SCORECARD_VERSION = scorecards.current_version()


def get_rubric(call_type: str, version: str = None) -> dict:
    return scorecards.config(call_type, version or SCORECARD_VERSION)


def get_config(call_type: str, version: str = None):
    cfg = get_rubric(call_type, version)
    return cfg['criteria'], cfg['section_pts'], cfg['section_names']


# ── Call type detection ───────────────────────────────────────────────────────
def detect_call_type(transcript: str, speaker_count: int, manual: str,
                     audio_duration_seconds: int = None) -> str:
    """
    Detect call type using a weighted scoring system derived from the actual
    V2.0 Simple checklist documents for BCM, BM and RCM.

    Each call type has a unique structural fingerprint drawn from its checklist:
    ─────────────────────────────────────────────────────────────────────
    BCM PHYSICAL PD (solo narrator, 15-25 min, field visit report):
      • Single speaker narrating what they SAW on the ground
      • Mentions visiting sites in person ("maine dekha", "gaya tha")
      • Describes business premises, stock, signboard physically
      • Conducts neighbour checks (business + home neighbours)
      • Views property papers with own eyes
      • Mentions photos taken (signboard, stock, QR, rooms)
      • References PRAGATI uploads
      • Gives own recommendation/view at the end (not a decision)

    BM AUDIO FI (2-party phone call, 5-15 min, identity/sourcing check):
      • Very short — genuineness check, NOT a deep PD
      • Starts with "yeh call record ho rahi hai"
      • BPS score reviewed before calling
      • Checks RM sourcing and LOGIN FEE
      • Asks about agent/broker/DSA/commission
      • Checks if customer knows the file exists
      • Ends with explicit FORWARD to CPA or REJECT at BM stage
      • Does NOT mention BCM visit (BCM hasn't gone yet at this stage)

    RCM AUDIO PD (2-party phone call, 10-20 min, deep income/property PD):
      • REFERENCES BCM's completed visit ("BCM ne visit kiya")
      • Cross-checks income against BCM assessment (CAM comparison)
      • AA (Account Aggregator) checks, UPI credits
      • Speaks to co-borrower on the call
      • Ends with APPROVE / APPROVE WITH CONDITIONS / REJECT decision note
      • Mentions next steps: soft sanction → KFS → disbursement
      • Mentions grievance channel, nodal officer
    ─────────────────────────────────────────────────────────────────────

    Priority:
    1. User explicit selection (never overridden)
    2. Structural scoring across all 3 types
    3. Speaker count as tiebreaker (1 speaker → BCM)
    4. Duration as secondary tiebreaker (short → BM, long → RCM)
    """
    # ── Priority 1: User explicit selection — ALWAYS WINS ────────────────────
    CANONICAL = {'BCM_PHYSICAL_PD', 'BM_AUDIO_FI', 'RCM_AUDIO_PD'}
    SHORT_MAP  = {'BCM': 'BCM_PHYSICAL_PD', 'BM': 'BM_AUDIO_FI', 'RCM': 'RCM_AUDIO_PD'}

    if manual and manual not in ('AUTO_DETECT', 'auto', '', 'None', 'null'):
        resolved = SHORT_MAP.get(manual, manual)
        if resolved in CANONICAL:
            print(f"detect_call_type: user-selected → {resolved}")
            return resolved

    t = transcript.lower()


    # ── Priority 2: Structural scoring from checklist vocabulary ────────────
    #
    # CRITICAL: Transcripts are in Devanagari Hindi script (Unicode).
    # Signals must match the actual text Amazon Transcribe produces.
    # Each signal list has (phrase, weight) tuples.
    # Higher weight = more unique/diagnostic for that call type.
    #
    # BCM PHYSICAL PD — solo narrator, field visit report, 15-25 min
    # Vocabulary from BCM V2.0 checklist "What to SAY in the recording"
    BCM_SIGNALS = [
        # BCM identification phrases — most unique BCM markers
        ('पीडी ऑब्जर्वेशन',        9),
        ('ऑब्जर्वेशन रिपोर्ट',     8),
        ('pd observation',          9),
        ('observation report',      8),
        ('फिजिकल पीडी',             8),
        ('physical pd',             8),
        # BCM narrates what he SAW — field visit narration
        ('मैंने देखा',              6),
        ('हमने देखा',               6),
        ('दुकान पर गए',             6),
        ('दुकान पर गया',            6),
        ('घर पर गए',                6),
        ('घर पर गया',               6),
        ('विजिट किया',              5),
        ('विजिट की',                5),
        ('साइट पर',                 4),
        # BCM neighbour checks — MUST items B8 + E3
        ('रेफरेंस में बात',          7),
        ('पड़ोसी से बात',            7),
        ('पड़ोसी ने बताया',          6),
        ('नेबर से बात',              6),
        ('दोनों पड़ोसी',             6),
        ('बिजनेस नेबर',              6),
        # BCM counts stock physically — item B5
        ('स्टॉक काउंट',              7),
        ('स्टॉक देखा',               6),
        ('स्टॉक गिना',               7),
        ('माल देखा',                 5),
        # BCM sees property papers physically — items F2/F3
        ('ओरिजिनल पेपर',             6),
        ('कागज देखे',                5),
        ('सेल डीड',                  6),
        ('पट्टा देखा',               6),
        # BCM PRAGATI uploads — item A5
        ('प्रगति पर',                5),
        ('प्रगति में',               5),
        ('फोटो ली',                  4),
        ('फोटो अपलोड',               5),
        # BCM solo narration markers
        ('बता रहा हूँ',              4),
        ('मेरी व्यू',                4),
        ('मेरा व्यू',                4),
        ('मेरी राय',                 4),
    ]

    # BM AUDIO FI — 2-party phone, SHORT (5-15 min), identity+sourcing check
    # Vocabulary from BM V2.0 checklist "What the BM must ASK on the call"
    BM_SIGNALS = [
        # BM opening — MUST item A1, most diagnostic BM phrase
        ('यह कॉल रिकॉर्ड हो रही',    9),
        ('ये कॉल रिकॉर्ड हो रही',     9),
        ('कॉल रिकॉर्ड हो रही है',     8),
        # BM reviews BPS before call — item A4
        ('बीपीएस स्कोर',              8),
        ('बीपीएस में',                7),
        ('bps score',                 8),
        # Login fee — MUST item B2, unique to BM
        ('लॉगिन फी',                  9),
        ('लॉगिन फीस',                 9),
        ('लॉगिन का पैसा',             8),
        ('login fee',                 9),
        # BM checks RM sourcing — section B
        ('आरएम ने विजिट',             7),
        ('आरएम आए थे',                7),
        ('आरएम का नाम',               6),
        ('rm ne visit',               7),
        # Broker/DSA/commission — MUST item B3
        ('ब्रोकर',                    7),
        ('डीएसए',                     7),
        ('broker',                    7),
        ('dsa',                       7),
        ('कमीशन माँगा',               7),
        ('जीरो कमीशन',                6),
        ('zero commission',           6),
        # BM FORWARD/REJECT decision — MUST item G3, most unique BM close
        ('फाइल आगे बढ़ाता',           9),
        ('फॉरवर्ड करता हूँ',           9),
        ('सीपीए को फॉरवर्ड',           9),
        ('forward karta',             9),
        ('cpa ko forward',            9),
        ('बीएम स्टेज',                8),
        ('रिजेक्ट कर रहा',            6),
        # BM checks application awareness — item A3
        ('फाइल लगाई',                 5),
        ('आवेदन लगाया',               5),
        ('लोन फाइल',                  5),
        # BM sufficiency check — MUST item D5
        ('30 हजार',                   6),
        ('30000',                     5),
        ('इनकम सफिशिएंट',             6),
    ]

    # RCM AUDIO PD — 2-party phone, LONGER (10-20 min), AFTER BCM visit
    # Vocabulary from RCM V2.0 checklist "What the RCM must ASK on the call"
    RCM_SIGNALS = [
        # RCM verifies BCM visit — MUST item B2, strongest RCM signal
        ('बीसीएम ने विजिट',           10),
        ('bcm ne visit',              10),
        ('बीसीएम गए थे',              9),
        ('बीसीएम ने देखा',             9),
        ('प्रोडिजी से कौन आए',         8),
        ('देखा था उन्होंने',            7),
        ('दुकान घर दोनों जगह देखा',    9),
        # AA checks — RCM MUST item D5
        ('अकाउंट एग्रीगेटर',           9),
        ('account aggregator',         9),
        ('एए चेक',                     8),
        ('aa check',                   8),
        ('यूपीआई क्रेडिट',             7),
        ('upi credits',                7),
        ('बाउंस',                      5),
        # RCM vs CAM comparison — section D
        ('कैम में',                     7),
        ('cam mein',                    7),
        ('बीसीएम की असेसमेंट',          8),
        ('bcm ki assessment',           8),
        # RCM property documents — specific to RCM deep-dive
        ('रिन पुस्तिका',               9),
        ('rin pustika',                9),
        ('नक्शा',                      6),
        ('रजिस्ट्री',                   5),
        ('गिरवी रखेंगे',               7),
        ('mortgage',                    5),
        # RCM disclosures — section I, unique to RCM
        ('सॉफ्ट सैंक्शन',              9),
        ('soft sanction',              9),
        ('केएफएस',                     8),
        ('kfs',                        8),
        ('की फैक्ट',                    8),
        ('डिस्बर्समेंट',               7),
        ('disbursement',               7),
        ('नाच मैंडेट',                  7),
        ('nach mandate',               7),
        # RCM closing decision
        ('अप्रूव',                      6),
        ('approve',                     6),
        ('कंडीशंस के साथ',             6),
        # RCM co-borrower on this call — MUST item F2
        ('पत्नी से बात',               7),
        ('वाइफ से बात',                7),
        ('co-borrower se baat',        7),
        # RCM grievance/next steps — section I3
        ('नोडल ऑफिसर',                 7),
        ('nodal officer',              7),
        ('ग्रीवेंस',                   6),
        ('grievance',                  6),
        ('सैंक्शन लेटर',               7),
        ('sanction letter',            7),
        ('शिकायत',                     5),
    ]

    def score_signals(signals):
        total = 0
        hits  = []
        for phrase, weight in signals:
            if phrase in t:
                total += weight
                hits.append(phrase)
        return total, hits

    bcm_score, bcm_hits = score_signals(BCM_SIGNALS)
    bm_score,  bm_hits  = score_signals(BM_SIGNALS)
    rcm_score, rcm_hits = score_signals(RCM_SIGNALS)

    print(f"detect_call_type scores: BCM={bcm_score}({len(bcm_hits)} hits) "
          f"BM={bm_score}({len(bm_hits)} hits) RCM={rcm_score}({len(rcm_hits)} hits) "
          f"speakers={speaker_count} dur={audio_duration_seconds}s manual={manual!r}")

    # ── Priority 3: Speaker count adjustment ─────────────────────────────────
    # BCM is fundamentally a solo narration. If only 1 speaker detected by
    # Transcribe diarization, boost BCM score significantly.
    if speaker_count is not None:
        if speaker_count <= 1:
            bcm_score += 15   # strong prior for single-speaker = BCM
        elif speaker_count >= 2:
            # Two-party call → BM or RCM only
            # Suppress BCM so it can't win purely on score
            bcm_score = max(0, bcm_score - 10)

    # ── Priority 4: Duration adjustment ──────────────────────────────────────
    # BM: 5-15 min | RCM: 10-20 min | BCM: 15-25 min
    if audio_duration_seconds:
        dur_min = audio_duration_seconds / 60
        if dur_min < 5:
            # Too short for RCM/BCM — lean BM (though likely invalid)
            bm_score  += 3
        elif dur_min <= 15:
            # BM range
            bm_score  += 5
        elif dur_min <= 20:
            # Overlaps RCM/BCM — slight RCM lean
            rcm_score += 3
        else:
            # > 20 min → BCM (BCM goes up to 25 min)
            bcm_score += 5

    # ── Final decision ────────────────────────────────────────────────────────
    scores = {'BCM_PHYSICAL_PD': bcm_score, 'BM_AUDIO_FI': bm_score, 'RCM_AUDIO_PD': rcm_score}
    winner = max(scores, key=scores.get)
    print(f"detect_call_type final: {winner} "
          f"(BCM={bcm_score} BM={bm_score} RCM={rcm_score})")
    return winner


# ── Bedrock call ──────────────────────────────────────────────────────────────
def call_bedrock(prompt: str, label: str, temperature: float = 0.0) -> str:
    body = {"messages":[{"role":"user","content":[{"text":prompt}]}],
            "inferenceConfig":{"max_new_tokens":MAX_OUTPUT_TOKENS,"temperature":temperature,"top_p":0.9}}
    if INTER_CALL_DELAY:
        time.sleep(INTER_CALL_DELAY)      # stay under the Bedrock rate quota
    print(f"[{label}] Calling Bedrock — {len(prompt)} chars")
    r = bedrock_runtime.invoke_model(modelId=BEDROCK_MODEL_ID, body=json.dumps(body))
    resp = json.loads(r['body'].read())
    text = resp['output']['message']['content'][0]['text']
    stop = resp.get('stopReason','?')
    print(f"[{label}] stop={stop}, output={len(text)} chars")
    if stop == 'max_tokens':
        raise Exception(f"[{label}] hit max_tokens — reduce prompt size")
    t = text.strip()
    if t.startswith('```'):
        lines = t.split('\n')
        s = next((i for i,l in enumerate(lines) if l.strip().startswith('{')), 0)
        e = next((i for i in range(len(lines)-1,-1,-1) if lines[i].strip().endswith('}')), len(lines)-1)
        t = '\n'.join(lines[s:e+1])
    return t


def _extract_json_object(text: str):
    """Parse JSON, falling back to the outermost {...} span if the model wrapped
    it in prose. Raises ValueError if nothing parseable is found."""
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find('{'), text.rfind('}')
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("no JSON object found in model output")


def call_bedrock_json(prompt: str, label: str, attempts: int = 3, temperature: float = 0.0):
    """
    call_bedrock + JSON parse, with retries.

    Every failure mode this pipeline has actually hit — a throttled/failed
    Bedrock call, output wrapped in prose, a truncated object — surfaces here as
    an exception, and previously meant a whole section silently defaulted to
    "No" (i.e. a real zero-score scorecard from a perfectly good recording).
    Retrying with an increasingly blunt "JSON only" instruction is cheap
    insurance against that.
    """
    last_err = None
    for attempt in range(1, attempts + 1):
        p = prompt if attempt == 1 else (
            prompt + "\n\nIMPORTANT: your previous response could not be parsed. "
                     "Respond with the raw JSON object ONLY — no prose, no markdown fences, "
                     "no commentary before or after."
        )
        try:
            lbl = label if attempt == 1 else f"{label}#retry{attempt - 1}"
            raw = call_bedrock(p, lbl, temperature) if temperature else call_bedrock(p, lbl)
            return _extract_json_object(raw)
        except Exception as e:
            last_err = e
            throttled = 'throttl' in str(e).lower() or 'too many requests' in str(e).lower()
            print(f"[{label}] attempt {attempt}/{attempts} failed"
                  f"{' (throttled)' if throttled else ''}: {str(e)[:160]}")
            if attempt < attempts:
                # This account's Bedrock quota is small and throttling is the
                # normal failure mode — it was the cause of whole sections
                # defaulting to "No" and publishing 0/100 scorecards. Wait long
                # enough to actually clear the rate window rather than burning
                # the retries in a couple of seconds. The Lambda has 900s.
                time.sleep(THROTTLE_BACKOFF[min(attempt - 1, len(THROTTLE_BACKOFF) - 1)]
                           if throttled else 2)
    raise Exception(f"[{label}] failed after {attempts} attempts: {last_err}")


# Models occasionally answer with a synonym or different casing than the four
# canonical values. Treating "yes" or "Full" as "not Yes" would silently zero a
# criterion the recording actually covered, so normalise before scoring.
_SCORE_ALIASES = {
    'yes': 'Yes', 'full': 'Yes', 'fully': 'Yes', 'fully covered': 'Yes', 'complete': 'Yes',
    'half': 'Half', 'partial': 'Half', 'partially': 'Half', 'partially covered': 'Half',
    'no': 'No', 'none': 'No', 'not covered': 'No', 'missing': 'No', 'absent': 'No',
    'n/a': 'N/A', 'na': 'N/A', 'not applicable': 'N/A',
}


def normalise_score(value) -> str:
    """Map whatever the model returned onto Yes/Half/No/N/A. Unknown -> 'No'."""
    if not isinstance(value, str):
        return 'No'
    v = value.strip()
    if v in ('Yes', 'Half', 'No', 'N/A'):
        return v
    return _SCORE_ALIASES.get(v.lower(), 'No')


# ── Evidence verification ─────────────────────────────────────────────────────
#
# Scores must be auditable: a reviewer has to be able to jump to a point in the
# recording and hear the thing that earned the marks. Asking the model for a
# timestamp does NOT achieve that — it has no timing data and simply invents
# plausible ones (the call summary has been emitting fabricated "[04:30]"
# stamps for exactly this reason).
#
# So the contract is split: the MODEL copies the exact words it scored on, and
# PYTHON finds those words in the diarised segments and reads off the real
# start time. The timestamp is therefore computed from Transcribe's own output
# and cannot be hallucinated. A quote that can't be located is reported as
# unverified rather than being dressed up with a number.

def _norm_text(s: str) -> str:
    if not isinstance(s, str):
        return ''
    s = s.lower()
    s = re.sub(r'[।|,\.\?!"\'`\-—–:;()\[\]{}]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def format_timestamp(seconds) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ''
    return f"{total // 60:02d}:{total % 60:02d}"


def verify_evidence(quote: str, segments: list) -> dict:
    """
    Locate `quote` in the diarised segments.

    Returns {'timestamp', 'verified', 'match': <how it matched>, 'speaker'}.
    'timestamp' is always taken from segment data, never from the model.

    A criterion often names several distinct sub-facts (e.g. "family income,
    who runs it, how it is verified"), and a single 40-word quote cannot carry
    proof for all of them when the recording covers them in different, widely
    separated sentences. The section prompt is allowed to cite several short
    quotes for one criterion, joined with " | " — each part is verified
    independently here, and the item is treated as verified (using the
    earliest-occurring part's timestamp) as soon as ANY part checks out, so a
    model does not lose credit for extra elements just because one of several
    quoted fragments does not match exactly.
    """
    blank = {'timestamp': '', 'verified': False, 'match': 'none', 'speaker': ''}
    if not quote or not segments:
        return blank

    if ' | ' in quote:
        parts = [p.strip() for p in quote.split(' | ') if p.strip()]
        if len(parts) > 1:
            results = [verify_evidence(p, segments) for p in parts]
            verified = [r for r in results if r['verified']]
            if verified:
                def _secs(r):
                    m = re.match(r'(\d+):(\d+)', r['timestamp'] or '')
                    return int(m.group(1)) * 60 + int(m.group(2)) if m else float('inf')
                best = min(verified, key=_secs)
                return {**best, 'match': f"multi({len(verified)}/{len(parts)})-{best['match']}"}
            # none of the parts verified — fall through and try the whole
            # string as one quote as a last resort.

    q = _norm_text(quote)
    # Too short to be proof. A two-word phrase like "गाय भैंस" occurs all over a
    # transcript, so the timestamp it resolves to would point at an arbitrary
    # occurrence rather than the moment that earned the marks. The prompt asks
    # for 10-25 words, so genuine evidence clears this easily.
    if len(q) < MIN_EVIDENCE_CHARS or len(q.split()) < MIN_EVIDENCE_TOKENS:
        return {**blank, 'match': 'too-short'}

    normed = [(_norm_text(s.get('text', '')), s) for s in segments]

    def hit(seg, how):
        return {'timestamp': format_timestamp(seg.get('start')), 'verified': True,
                'match': how, 'speaker': seg.get('speaker', '')}

    # 1. Quote sits inside a single segment. If the same words occur in more
    #    than one place, say so — the first occurrence is returned, but the
    #    reviewer should know the location is not unique.
    exact_hits = [seg for nt, seg in normed if nt and q in nt]
    if exact_hits:
        how = 'exact' if len(exact_hits) == 1 else f'exact-x{len(exact_hits)}'
        return hit(exact_hits[0], how)

    # 2. Quote spans consecutive segments — search the joined text and map the
    #    match offset back to the segment it starts in.
    offsets, parts, pos = [], [], 0
    for nt, seg in normed:
        offsets.append((pos, seg))
        parts.append(nt)
        pos += len(nt) + 1
    joined = ' '.join(parts)
    idx = joined.find(q)
    if idx != -1:
        start_seg = offsets[0][1]
        for off, seg in offsets:
            if off <= idx:
                start_seg = seg
            else:
                break
        return hit(start_seg, 'exact-span')

    # 3. The model sometimes pastes a long passage spanning many segments
    #    instead of the short quote it was asked for. Anchor on the opening
    #    words — the timestamp should mark where the quoted passage begins.
    head = ' '.join(q.split()[:12])
    if len(head) >= MIN_EVIDENCE_CHARS and head != q:
        for nt, seg in normed:
            if nt and head in nt:
                return hit(seg, 'exact-head')
        idx = joined.find(head)
        if idx != -1:
            start_seg = offsets[0][1]
            for off, seg in offsets:
                if off <= idx:
                    start_seg = seg
                else:
                    break
            return hit(start_seg, 'exact-head-span')

    # 4. Transcribe output and the model's copy can differ slightly (numerals,
    #    fillers). Fall back to best token overlap over windows of consecutive
    #    segments, sized to the length of the quote.
    q_tokens = set(q.split())
    if not q_tokens:
        return blank
    # A long quote legitimately spans many segments, so widen the window rather
    # than declaring it unverifiable.
    max_window = max(3, min(12, len(q.split()) // 8 + 1))
    best_ratio, best_seg = 0.0, None
    for i in range(len(normed)):
        for window in range(1, max_window + 1):
            if i + window > len(normed):
                break
            tokens = set(' '.join(nt for nt, _ in normed[i:i + window]).split())
            if not tokens:
                continue
            ratio = len(q_tokens & tokens) / len(q_tokens)
            if ratio > best_ratio:
                best_ratio, best_seg = ratio, normed[i][1]
    if best_seg is not None and best_ratio >= 0.6:
        return hit(best_seg, f'fuzzy-{best_ratio:.2f}')

    return blank


# The scoring prompts show the model a "[MM:SS] spk_0: words" transcript, and
# models sometimes paste that prefix into a quote. Strip it before matching.
_LINE_PREFIX = re.compile(r'\[\s*\d{1,2}:\d{2}(?::\d{2})?\s*\]\s*(?:spk_\d+|speaker[_ ]?\d+)?\s*:?\s*',
                          re.I)


def clean_quote(quote) -> str:
    if not isinstance(quote, str):
        return ''
    return _LINE_PREFIX.sub(' ', quote).strip(' "\'|')


# An element's quote must carry its own context: a four-word fragment such as
# "कोई लोन नहीं है" occurs all over a call and proved "the property is not
# mortgaged" on a production RCM call where that was never asked.
MIN_ELEMENT_TOKENS = 5

# Checks against documents the evaluator does not have (CAM, PD report,
# bureau, AA) are never elements — the prompt says so, and this enforces it.
_DOCUMENT_CHECK = re.compile(r'\bcam\b|pd report|technical report|bureau|within\s*\d+\s*%', re.I)


def _long_enough(quote: str) -> bool:
    return len(_norm_text(quote).split()) >= MIN_ELEMENT_TOKENS


# Element statuses that credit a criterion.
_CREDIT = {'covered', 'stated_absent'}
_PARTIAL = {'partial'}


def score_from_elements(item: dict, criterion: dict, segments: list) -> dict:
    """
    Re-derive a criterion's mark from its element-by-element breakdown.

    The model lists every element the criterion requires, with a verbatim
    quote for each one it credits. Each quote is looked up in the diarised
    segments; an element whose quote is not in the recording is treated as
    missing. The mark then follows the scorecard's own definition: every
    element covered -> Yes, some -> Half, none -> No. This is what stops a
    single loose (or invented) quote from carrying a whole criterion, and a
    long call's early or late remarks from being overlooked.

    A "stated_absent" element counts as covered only where the scorecard says
    a clear statement of absence is coverage; elsewhere it shows the subject
    came up, so it counts as partial.
    """
    elements = [e for e in (item.get('elements') or []) if isinstance(e, dict)
                and not _DOCUMENT_CHECK.search(str(e.get('element', '')))]
    if not elements:
        item.pop('elements', None)
        return item
    absence_ok = bool(criterion.get('absence_counts'))
    checked, quotes, dropped = [], [], []
    for e in elements:
        status = str(e.get('status', 'missing')).strip().lower().replace(' ', '_')
        if status not in ('covered', 'partial', 'stated_absent', 'missing'):
            status = 'missing'
        quote = clean_quote(e.get('quote'))
        ev = verify_evidence(quote, segments) if quote and status != 'missing' else \
            {'verified': False, 'timestamp': '', 'match': 'none', 'speaker': ''}
        if status != 'missing' and not (ev['verified'] and _long_enough(quote)):
            dropped.append(str(e.get('element', ''))[:60])
            status = 'missing'
        if status == 'stated_absent' and not absence_ok:
            status = 'partial'
        checked.append({'element': str(e.get('element', ''))[:160], 'status': status,
                        'quote': quote[:300] if status != 'missing' else '',
                        'timestamp': ev['timestamp'] if status != 'missing' else ''})
        if status != 'missing':
            quotes.append(quote)

    item['model_score'] = item.get('score')
    item['elements'] = checked
    derive_from_elements(item)
    if dropped:
        item['unverified_elements'] = dropped
        note = (item.get('note') or '').strip()
        item['note'] = (f"{note} [cited words not found in the recording for: "
                        f"{', '.join(dropped)}]").strip()[:250]
    return item


def derive_from_elements(item: dict) -> dict:
    """Set the mark and evidence from already-verified elements."""
    checked = item.get('elements') or []
    if not checked:
        return item
    credited = sum(1 for e in checked if e['status'] in _CREDIT)
    touched = credited + sum(1 for e in checked if e['status'] in _PARTIAL)
    item['score'] = 'Yes' if credited == len(checked) else ('Half' if touched else 'No')
    item['evidence'] = ' | '.join(e['quote'] for e in checked if e['status'] != 'missing' and e['quote'])
    return item


def score_section_items(sdata: dict, sec_criteria: list, segments: list) -> tuple:
    """Parse one scoring response into verified items. Returns (items, missing_ids)."""
    cmap = {c['id']: c for c in sec_criteria}
    seen, items = set(), []
    for i in sdata.get('items', []):
        if not isinstance(i, dict) or i.get('id') not in cmap or i.get('id') in seen:
            continue
        seen.add(i['id'])
        item = {**i, 'score': normalise_score(i.get('score'))}
        score_from_elements(item, cmap[i['id']], segments)
        quote = clean_quote(item.get('evidence'))
        # Timestamp is looked up from the diarised segments using the model's
        # quote — never taken from the model itself.
        ev = verify_evidence(quote, segments) if quote else \
            {'timestamp': '', 'verified': False, 'match': 'none', 'speaker': ''}
        item['evidence']           = quote
        item['evidence_timestamp'] = ev['timestamp']
        item['evidence_verified']  = ev['verified']
        item['evidence_match']     = ev['match']
        item['evidence_speaker']   = ev['speaker']
        items.append(item)
    return items, [c['id'] for c in sec_criteria if c['id'] not in seen]


# Independent scoring passes per batch. On long calls (30+ minutes) a single
# pass sometimes collapses — every criterion in a batch comes back "missing"
# with nothing quoted, although the same prompt credits them on another run.
# A second pass (different sampling, criteria in reverse order) is merged per
# criterion, keeping the one with more VERIFIED coverage. That cannot inflate
# a mark on invented evidence: every credited element must still quote words
# that are found in the recording, and the element audit then checks that each
# quote is about the right thing.
SCORING_PASSES = int(os.environ.get('SCORING_PASSES', '2'))
SECOND_PASS_TEMPERATURE = float(os.environ.get('SECOND_PASS_TEMPERATURE', '0.3'))

_MARK_RANK = {'No': 0, 'N/A': 0, 'Half': 1, 'Yes': 2}


def _coverage(item: dict) -> tuple:
    """How much verified coverage an item carries — used to merge passes."""
    els = item.get('elements') or []
    credited = sum(1 for e in els if e['status'] in _CREDIT)
    partial = sum(1 for e in els if e['status'] in _PARTIAL)
    return (_MARK_RANK.get(item.get('score'), 0), credited, partial)


def merge_passes(first: list, second: list) -> list:
    by_id = {i['id']: i for i in second}
    merged = []
    for item in first:
        other = by_id.get(item['id'])
        if other and not other.get('defaulted') and (
                item.get('defaulted') or _coverage(other) > _coverage(item)):
            other['won_second_pass'] = True
            merged.append(other)
        else:
            merged.append(item)
    return merged


# Criteria per scoring call. A long section (BCM "Business" has 9) is split so
# every criterion gets the model's full attention and the element-level output
# stays well inside the response limit.
SCORING_BATCH = int(os.environ.get('SCORING_BATCH', '5'))


def scoring_batches(criteria: list, section_id: str) -> list:
    sec = [c for c in criteria if c['sec'] == section_id]
    return [sec[i:i + SCORING_BATCH] for i in range(0, len(sec), SCORING_BATCH)]


# ── Call type classification (LLM — primary path) ─────────────────────────────
#
# The keyword+speaker-count heuristic in detect_call_type() above is kept as a
# fallback, but it misclassifies real recordings: BCM field visits are often a
# live in-person interview (officer + customer + family, 2-3 diarized
# speakers), not a solo narration, and BCM officers routinely discuss the same
# generic loan topics (commission, login fee) that were weighted as BM-only
# signals. A real transcript with detailed in-person property/family/dairy
# questioning got misclassified as BM_AUDIO_FI purely because of one early
# mention of "commission" plus a 3-speaker diarization penalizing BCM.
# Classifying with the model directly — which has already proven it reads
# these transcripts accurately — is far more robust than hand-tuned keyword
# weights.
def build_calltype_classification_prompt(transcript: str, speaker_count: int,
                                         audio_duration_seconds: int) -> str:
    dur = f"{audio_duration_seconds//60}:{audio_duration_seconds%60:02d}" if audio_duration_seconds else "not measured"
    return f"""You are classifying a Prodigee Finance loan-officer audio recording into exactly
one of three call types. Read the ENTIRE transcript below before deciding — it is in Hindi
(Devanagari script), often mixed with English/Hinglish.

Diarization detected {speaker_count} speaker(s). Measured duration: {dur}.
Note: diarization speaker counts are unreliable (background voices, noise, or a single person's
tone shifts can be split into extra "speakers") — do NOT treat speaker count as decisive.

THE SINGLE MOST IMPORTANT QUESTION: is the officer PHYSICALLY PRESENT with the customer right
now (in-person visit), or is this a PHONE CALL (officer elsewhere, reviewing the case remotely)?
Look for direct tells:
  - Phone-call tells: a "hello, kaise ho" greeting typical of answering an incoming call; the
    officer referring to reviewing a report/bureau/CIBIL/register/screen ("mujhe dikh raha hai",
    "civil mein dikh raha hai") rather than physically seeing objects; no mention of walking
    around, going somewhere next, or taking photos right now.
  - In-person tells: talk of going to the customer's home NEXT after finishing at the shop,
    taking photos there right now, meeting family members face to face as they arrive, counting
    stock/animals/land by eye, discussing what neighbours just said in person.
Both BCM (in-person) and RCM (phone, deep verification) can be long, detailed, and cover
business + income + property + loans — depth and length alone do NOT distinguish them. The
in-person vs phone-call tells above are what matters.

If it IS a phone call, the next question is WHERE IN THE PROCESS this call sits — this is what
separates BM from RCM, and both duration and depth matter here (unlike the BCM-vs-phone question
above, where depth alone is not decisive):

  - BM_AUDIO_FI happens FIRST, right after the customer's loan file is logged, BEFORE any
    physical visit has happened. Telltale: the officer may say the visit will happen LATER
    ("ek-do din mein visit pe aa jaayenge", "BCM visit karega") — a call that says the visit is
    still upcoming CANNOT be RCM. It is typically very short (often just 2-8 minutes): a light,
    single-pass check of identity/business/rough numbers, ending with a simple FORWARD-the-file
    or REJECT-at-this-stage decision. It does NOT go loan-by-loan through a bureau report, does
    not correct or dispute specific existing EMI figures, and does not value or scrutinise
    property documents in detail — a passing mention of a plot/house is fine, granular
    verification of it is not.

  - RCM_AUDIO_PD happens LATER, once substantial data on the case already exists (from a prior
    visit and/or bureau pull), and the officer does GRANULAR VERIFICATION against that existing
    data: naming multiple SPECIFIC existing loans with their precise EMI amounts and checking
    each one, correcting or querying discrepancies against a bureau/CIBIL/report already in front
    of them, scrutinising specific property document types or figures, and reaching a
    substantive, considered decision (approve / reject / conditions) rather than a quick forward.
    It is typically longer (often 10+ minutes) because of this depth.

THE THREE CALL TYPES, in this priority order — check each in turn, first match wins:

1. RCM_AUDIO_PD — see the phone-call timing/depth test above: a LATER-stage phone call doing
   granular loan-by-loan / document-by-document verification against existing data, ending in a
   substantive credit decision.

2. BM_AUDIO_FI — see the phone-call timing/depth test above: a FIRST-stage, short, light-touch
   phone call (identity, rough business/income, broker/commission, login fee) ending in a simple
   forward/reject-at-this-stage decision, often explicitly BEFORE a visit has happened.

3. BCM_PHYSICAL_PD — an IN-PERSON field visit (see tells above). Can be a solo wrap-up narration
   OR a live interview recorded during the visit itself. Everything that is not clearly a
   phone call falls here: deep in-person coverage of business, farm/dairy, family, property and
   loans, gathered face to face at the shop/home.

TRANSCRIPT:
{transcript}

First, answer explicitly:
(a) does the transcript OPEN with a phone-answering greeting ("hello", "haan ji", "namaste ...
    kaise ho" said as if picking up a call)?
(b) does the officer say the physical visit is still UPCOMING (not yet happened)?
(c) does the officer go through multiple SPECIFIC existing loans/EMI figures one by one against a
    bureau/report already pulled up, or scrutinise specific property documents in detail?
(a) is the main signal for phone vs in-person. (b) being true rules out RCM. (c) being true (deep,
named, figure-by-figure verification) points to RCM; (c) being false/shallow, combined with a
short call, points to BM.

Return ONLY valid JSON (no markdown fences):
{{
  "opens_with_phone_greeting": true|false,
  "visit_still_upcoming": true|false,
  "granular_loan_or_document_verification": true|false,
  "call_type": "BCM_PHYSICAL_PD|BM_AUDIO_FI|RCM_AUDIO_PD",
  "confidence": "high|medium|low",
  "reason": "<one sentence citing the specific tell and content that decided it>"
}}"""


def classify_call_type(transcript: str, speaker_count: int, manual: str,
                       audio_duration_seconds: int = None) -> dict:
    """
    Returns {'call_type', 'confidence', 'reason', 'method'}.

    Priority:
    1. User explicit selection — always wins, no model call needed.
    2. LLM classification of the full transcript (primary path — far more
       reliable than keyword heuristics, see build_calltype_classification_prompt).
    3. Keyword+speaker-count heuristic (detect_call_type) as a fallback, only
       if the LLM call fails or returns something unparseable.

    The confidence and reason are carried through to the stored result so a
    wrong call type (which silently invalidates the whole scorecard, since it
    picks the rubric) can be audited after the fact instead of being invisible.
    """
    CANONICAL = {'BCM_PHYSICAL_PD', 'BM_AUDIO_FI', 'RCM_AUDIO_PD'}
    SHORT_MAP  = {'BCM': 'BCM_PHYSICAL_PD', 'BM': 'BM_AUDIO_FI', 'RCM': 'RCM_AUDIO_PD'}

    if manual and manual not in ('AUTO_DETECT', 'auto', '', 'None', 'null'):
        resolved = SHORT_MAP.get(manual, manual)
        if resolved in CANONICAL:
            print(f"classify_call_type: user-selected -> {resolved}")
            return {'call_type': resolved, 'confidence': 'user-selected',
                    'reason': 'Call type explicitly selected at upload', 'method': 'manual'}

    try:
        cp = build_calltype_classification_prompt(transcript, speaker_count, audio_duration_seconds)
        cdata = call_bedrock_json(cp, "CLASSIFY")
        result = cdata.get('call_type')
        if result in CANONICAL:
            confidence = str(cdata.get('confidence', 'unknown'))
            reason     = str(cdata.get('reason', ''))[:400]
            print(f"classify_call_type: LLM -> {result} (confidence={confidence}, reason={reason})")
            return {'call_type': result, 'confidence': confidence,
                    'reason': reason, 'method': 'llm'}
        print(f"classify_call_type: LLM returned unrecognised call_type {result!r} — falling back")
        fallback_reason = f"LLM returned unrecognised call_type {result!r}"
    except Exception as e:
        print(f"classify_call_type: LLM classification failed ({e}) — falling back to heuristic")
        fallback_reason = f"LLM classification failed: {e}"

    heuristic = detect_call_type(transcript, speaker_count, manual, audio_duration_seconds)
    return {'call_type': heuristic, 'confidence': 'low',
            'reason': f'Keyword-heuristic fallback used ({fallback_reason})',
            'method': 'heuristic-fallback'}


# ── Evaluation prompt builders ────────────────────────────────────────────────
#
# IMPORTANT: Scoring is done ONE SECTION AT A TIME (not all 26-35 criteria in a
# single call). Testing against real production transcripts showed that when
# asked to judge every criterion in one giant prompt, the model would collapse
# into a lazy "not covered" template for every single item — even when the
# transcript clearly contained the relevant information (verified by asking
# the same model simple direct questions about the same transcript, which it
# answered correctly). Scoping each call to one section (4-9 items) keeps the
# model actually engaged and grounded in the transcript instead of pattern-
# completing a rejection.
def type_note(call_type: str, version: str = None) -> str:
    return get_rubric(call_type, version).get('type_note', '')


def build_header_prompt(transcript: str, call_type: str, dur_secs: int,
                        version: str = None) -> str:
    dur = f"{dur_secs//60}:{dur_secs%60:02d}" if dur_secs else "not measured"
    return f"""You are the Audio Evaluation Agent for Prodigee Finance Limited.
Read this {call_type} recording transcript carefully (Hindi/Hinglish, Devanagari script — evaluate by meaning).
{type_note(call_type, version)}
Recording duration measured: {dur}

TRANSCRIPT:
{transcript}

Extract the header details. Search the whole transcript before writing "Not stated" — names,
IDs and amounts are often said mid-conversation, not just at the start.

Return ONLY valid JSON (no markdown fences):
{{
  "application_id": "<stated or N/A>",
  "customer_name": "<as spoken>",
  "officer_name": "<BCM/BM/RCM name or Not stated>",
  "loan_amount": "<as spoken or N/A>",
  "recommendation": "<exact words or Not stated>",
  "duration_spoken": "<as stated or Not stated>",
  "duration_measured": "{dur}"
}}"""


def build_self_audio_check_prompt(transcript: str) -> str:
    """
    A BCM Physical PD submission is a SELF-AUDIO recording: the BCM records
    himself narrating what he saw, checked and concluded after the visit. The
    whole BCM checklist is written as "What to SAY in the recording" — "what you
    SAW", "YOUR VIEW", "photos you took" — and none of it can be marked against
    a recording of the officer interviewing the customer.

    The client returned our BCM scorecard unmarked for exactly this reason:
    "The Transcript is not a Monologue. It is rather an interview with the
    customer and hence the BCM Evaluation must not be done as it was not a
    Self-Audio Recording."

    So before scoring a BCM submission we establish which kind of recording it
    is, and refuse to score an interview rather than producing a scorecard that
    looks authoritative and is meaningless.
    """
    return f"""You are checking what KIND of recording this is, before it is evaluated.

A Prodigee "BCM Physical PD" submission must be a SELF-AUDIO RECORDING: the BCM officer records
himself, alone, narrating his own observations after visiting the customer — what he saw at the
business and home, who he met, what documents he checked, what he concluded. It is a report
spoken to the recorder, not a conversation.

It is NOT a valid self-audio recording if it is an INTERVIEW: the officer asking the customer
questions and the customer answering ("आपका नाम क्या है?" ... "कितना लोन चाहिए?" ... "जी सर"),
whether in person or on the phone. A question-and-answer conversation is an interview even if
the officer is physically at the customer's premises.

Decide which this transcript is. Base it on the SHAPE of the language:
- Interview  → questions addressed to another person, answers coming back, second-person address.
- Self-audio → continuous first-person narration by one person describing what he did and saw,
               with no one answering him.
- Reported speech is NOT an interview. A BCM telling the recorder what the customer said, in the
  third person ("उन्होंने बताया कि…", "राजू जी ने बताया…", "इनकी आय … है", "इनके पास तीन ऑटो हैं"),
  is a self-audio report.
- To call it an interview, your "evidence" must be an actual question put to the customer and
  the customer's answer, copied verbatim. If you cannot quote such an exchange, it is self-audio.

TRANSCRIPT:
{transcript}

Return ONLY valid JSON (no markdown fences):
{{
  "recording_kind": "self_audio_monologue|interview_with_customer",
  "confidence": "high|medium|low",
  "reason": "<one sentence>",
  "evidence": "<a short exact quote from the transcript that shows which it is>"
}}"""


def check_bcm_self_audio(transcript: str, segments: list) -> dict:
    """Returns {'is_self_audio', 'kind', 'confidence', 'reason', 'evidence'}."""
    try:
        d = call_bedrock_json(build_self_audio_check_prompt(transcript), "SELF-AUDIO")
    except Exception as e:
        # Never block an evaluation because this check failed; let it proceed
        # and let the normal review flags do their job.
        print(f"Self-audio check failed: {e} — proceeding with evaluation")
        return {'is_self_audio': True, 'kind': 'unknown', 'confidence': 'low',
                'reason': f'check failed: {e}', 'evidence': ''}
    kind = str(d.get('recording_kind', '')).strip().lower()
    ev   = verify_evidence((d.get('evidence') or '').strip(), segments)
    result = {
        'is_self_audio': kind != 'interview_with_customer',
        'kind': kind or 'unknown',
        'confidence': str(d.get('confidence', 'unknown')),
        'reason': str(d.get('reason', ''))[:300],
        'evidence': (d.get('evidence') or '').strip()[:300],
        'evidence_timestamp': ev['timestamp'],
    }
    # The model's verdict refuses the whole recording, so it is only accepted
    # on evidence. A client BCM narration (one voice, not a single question)
    # was refused as an "interview" on a quote that was plain narration.
    if not result['is_self_audio']:
        speakers = {s.get('speaker') for s in segments if s.get('speaker')}
        if len(speakers) <= 1:
            result.update(is_self_audio=True, overridden='one_speaker',
                          override_reason='Transcribe detected a single voice — an interview needs two')
        elif not (ev['verified'] and _SELF_AUDIO_QA.search(result['evidence'])):
            result.update(is_self_audio=True, overridden='no_qa_evidence',
                          override_reason='the quoted evidence is not a question-and-answer exchange '
                                          'found in this recording')
    print(f"Self-audio check: kind={result['kind']} confidence={result['confidence']} "
          f"reason={result['reason']}"
          + (f" | OVERRIDDEN: {result['override_reason']}" if result.get('overridden') else ''))
    return result


# Marks of a question put to the customer: a question mark, or second-person
# address ("आप…", "तुम", "बताइए"). Third-person narration has none of these.
_SELF_AUDIO_QA = re.compile(r'\?|आप|तुम|तुम्ह|बताइ|बताएं|बतायें|बोलिए|बोलो')


def build_profile_prompt(transcript: str, call_type: str) -> str:
    """
    Establish what the borrower actually HAS before scoring.

    A section prompt sees only its own criteria and cannot distinguish "this
    borrower has no shop" from "the shop was never discussed" — so it scores
    shop criteria No either way, and a salaried borrower gets marked down on
    all 30 points of the Business section for failing to describe a shop that
    does not exist. Those points belong in N/A (excluded from the denominator),
    and deciding that needs a view of the whole transcript at once.
    """
    return f"""You are the Audio Evaluation Agent for Prodigee Finance Limited.
Read this {call_type} recording transcript (Hindi/Devanagari, mixed with English) and establish
what this borrower's situation actually IS. This is used to decide which scoring criteria apply
to them at all — it is NOT a judgement of the officer.

Answer about the HOUSEHOLD, not only the applicant personally: if the land, business or animals
belong to the father, wife or another family member, that still counts as "yes" — the criteria
ask about family assets too.

For each question answer exactly "yes", "no" or "unclear":
- "yes"     = the recording shows the household has this
- "no"      = the recording positively indicates the household does NOT have this (e.g. the
              borrower is salaried and no shop of any kind is ever referred to)
- "unclear" = you genuinely cannot tell from this recording

Be careful and conservative: answer "no" only when the recording actually indicates absence.
If the subject simply never comes up and you cannot tell either way, answer "unclear".

TRANSCRIPT:
{transcript}

Return ONLY valid JSON (no markdown fences):
{{
  "has_shop_or_trading_business": "yes|no|unclear",
  "has_farm_land": "yes|no|unclear",
  "has_dairy_or_livestock": "yes|no|unclear",
  "is_salaried_or_job": "yes|no|unclear",
  "has_co_borrower_or_spouse_involved": "yes|no|unclear",
  "main_income_source": "<short phrase, e.g. 'salaried job + dairy'>"
}}"""


def render_profile(profile: dict) -> str:
    """Turn the profile into a short block for the section prompts."""
    if not profile:
        return ''
    labels = {
        'has_shop_or_trading_business':       'Runs a shop / trading business',
        'has_farm_land':                      'Has farm land',
        'has_dairy_or_livestock':             'Has dairy animals / livestock',
        'is_salaried_or_job':                 'Has a salaried job',
        'has_co_borrower_or_spouse_involved': 'Has a co-borrower / spouse involved',
    }
    lines = [f"  - {text}: {str(profile.get(key, 'unclear')).upper()}"
             for key, text in labels.items()]
    main = profile.get('main_income_source')
    if main:
        lines.append(f"  - Main income source: {main}")
    return ("BORROWER SITUATION (established from the full recording — background for your "
            "scoring):\n" + '\n'.join(lines) + """
Use this only as context for understanding the conversation. Note in particular that a criterion
can still be scored on what the officer DID cover even if the borrower's situation is unusual —
e.g. if the household has no shop, the officer is still expected to establish and state that.
Score every criterion Yes / Half / No on what was actually said.
""")


def has_other_income(profile: dict) -> bool:
    """
    Value for the BCM scorecard's E6 cell — "Farm / dairy / other family income
    in this case? (Yes/No)".

    This is the client template's OWN applicability mechanism: when E6 is "No",
    the sheet computes points applicable as 100-10, dropping section C entirely
    (`=100-IF(E6="No",10,0)`). It is the only per-section allowance any of the
    three templates has, so it is the only one we apply — scoring individual
    items N/A would diverge from the sheet the client actually reviews, where
    an "N/A" mark simply scores zero.
    """
    if not profile:
        return True
    answers = [str(profile.get(k, 'unclear')).strip().lower()
               for k in ('has_farm_land', 'has_dairy_or_livestock')]
    # Drop section C only on positive evidence that there is neither farm nor
    # dairy income. Anything else — a "yes", or an "unclear" — keeps it in the
    # denominator, because dropping it on a guess hands over 10 free points.
    return not all(a == 'no' for a in answers)


def format_criterion(c: dict) -> str:
    """One criterion as the prompts show it, with its scorecard-specific rule."""
    line = f"  {c['id']} [{c['pts']}pts{'  ★MUST' if c['must'] else ''}]: {c['text']}"
    if c.get('absence_counts'):
        line += "\n      [A clear statement that this does NOT exist counts as fully covered → Yes]"
    if c.get('guidance'):
        line += f"\n      How to score: {c['guidance']}"
    return line


def rubric_rules_block(rubric: dict, client_version: str) -> str:
    """The scorecard-wide rules from the client's checklist document."""
    rules = list(rubric.get('general_rules') or [])
    if rubric.get('absence_rule'):
        rules.insert(0, rubric['absence_rule'])
    if not rules:
        return ''
    body = '\n'.join(f"- {r}" for r in rules)
    return f"""
SCORECARD RULES ({client_version}) — these come from the client's checklist document and
override anything below that seems to say otherwise:
{body}
"""


# Legacy scorecards had no rule for a topic that does not exist, so the model
# was told to leave it alone; from v1 a clearly stated absence is full coverage
# and silence scores zero. Legacy wording is kept verbatim so legacy scoring
# does not move.
_NA_RULE_LEGACY = """There is NO "N/A" mark. The client's scorecard has only Yes / Half / No, and an N/A would simply
score zero while still counting against the total — so never use it. Where a whole topic does not
apply to this borrower (no farm, no dairy), that is handled elsewhere and is not your concern:
score what the officer did or did not cover."""

_NA_RULE_ABSENCE = """There is NO "N/A" mark. The client's scorecard has only Yes / Half / No, and an N/A would simply
score zero while still counting against the total — so never use it. Where a topic does not exist
for this borrower (no farm, no dairy, no other income, no defaults), the SCORECARD RULES above
decide it: a clear statement that it does not exist is Yes; never mentioning it is No."""

_ABSENCE_EXAMPLES_LEGACY = """  Criterion: "STOCK: you counted stock yourself — your value vs customer claim."
  Recording: the borrower is salaried with dairy animals and has no shop or stock at all.
  → Correct: N/A (it cannot apply). WRONG: No.

  Criterion: "2 BUSINESS NEIGHBOURS: name, shop, mobile, what they said."
  Recording: the borrower does have a shop, but the officer never speaks to any neighbour.
  → Correct: No (it applies and was genuinely not done). WRONG: N/A."""

_ABSENCE_EXAMPLES = """  Criterion: "LAND: is there agriculture land — YES or NO? If land: acres, whose name, who cultivates."
  Recording: "इनके पास कोई खेती की ज़मीन नहीं है" — the officer says clearly there is no land.
  → Correct: Yes (a clear statement of absence is full coverage). WRONG: No, Half or N/A.

  Criterion: "DAIRY: any dairy animals — YES or NO? If yes: how many milking, litres per day, sold where."
  Recording: dairy / animals are never mentioned at all.
  → Correct: No (silence is not a statement of absence). WRONG: Yes or N/A.

  Criterion: "All ACTIVE loans in the CRIF — lender, EMI, outstanding — discussed. Closed loans need NOT be discussed."
  Recording: both running loans are discussed with lender and EMI; an old closed loan is not mentioned.
  → Correct: Yes (the closed loan is not required). WRONG: Half."""


def build_section_prompt(transcript: str, call_type: str, section_id: str,
                         section_criteria: list, section_name: str, dur_secs: int,
                         profile: dict = None, version: str = None) -> str:
    dur = f"{dur_secs//60}:{dur_secs%60:02d}" if dur_secs else "not measured"
    n   = len(section_criteria)
    rubric = get_rubric(call_type, version)
    client_version = scorecards.load(version or SCORECARD_VERSION)['client_version']
    absence = bool(rubric.get('absence_rule'))
    na_rule = _NA_RULE_ABSENCE if absence else _NA_RULE_LEGACY
    absence_examples = _ABSENCE_EXAMPLES if absence else _ABSENCE_EXAMPLES_LEGACY

    must_ids = [c['id'] for c in section_criteria if c['must']]
    must_str = ', '.join(must_ids) if must_ids else 'none in this section'

    crit_list = '\n'.join(format_criterion(c) for c in section_criteria)

    return f"""You are the Audio Evaluation Agent for Prodigee Finance Limited.
Evaluate this {call_type} recording transcript against the {n} criteria below, drawn from
SECTION {section_id} ({section_name}) of the {client_version} framework.
Recording duration: {dur}
{type_note(call_type, version)}

Read the ENTIRE transcript below carefully before scoring — it is a real recorded conversation
in Hindi (Devanagari script), often mixed with English/Hinglish terms. Information relevant to a
criterion may be spoken informally, out of order, or using different words than the checklist —
search thoroughly by MEANING, not by matching exact English words, before deciding a criterion is
not covered.

PARSING DENSE NARRATION — BCM/BM self-audio recordings are usually ASR output with little or no
punctuation: one unbroken sentence routinely carries 4-6 unrelated facts back to back (e.g. the
customer's village, family size, income type, and animal count can all sit inside a single
run-on sentence with no full stop between them). Do not read such a sentence as one fact and move
on — break it into its separate atomic facts first, then check EACH fact against every criterion
it could be relevant to. A criterion's evidence is very often buried mid-sentence between two
unrelated facts, not set off on its own — that does not make it any less valid.

NUMBERS THAT ARE STATED MORE THAN ONCE — when the same subject (an amount, a count, an income
figure) is given more than one value at different points in the recording, this is common and
important, not noise to smooth over:
  - If a LATER statement is an explicit correction, cross-check or verification of an EARLIER one
    (e.g. the officer physically counts the animals and finds fewer than what a register/diary/
    the customer originally claimed), score the criterion using the LATER, verified figure — but
    keep BOTH figures in your "note" (e.g. "diary claimed 33-35L; officer's own count of the
    animals present gives ~12L/day — discrepancy flagged"). Do not silently keep only one number.
  - Do NOT average, reconcile or "clean up" two different figures into one — if they conflict,
    report the conflict.
  - A discrepancy the officer THEMSELVES caught and reported (a document that doesn't match what
    they saw on the ground, an inflated figure, a corrected claim) is exactly the kind of
    diligence the "your view", genuineness and proof-related criteria exist to reward — score it
    as full or strong coverage of that criterion, not as a shortfall, and it is also material for
    the red-flags pass even where the officer already flagged it.

SCORING RULES — read these carefully, they are applied wrongly more often than anything else.

Each criterion below lists SEVERAL things (often 4-6). Almost no real recording covers every one
of them, and scoring such a criterion "No" is wrong — that is what "Half" exists for.

Read the criterion as a LIST of the things it names, then decide:

- Yes  = essentially ALL of the named things were covered, with the actual names / numbers /
         specifics. → full points
- Half = the subject WAS covered, but one or more of the named things is missing, or the answers
         stayed vague. This is the normal mark for real recordings — officers routinely cover a
         topic without every element of it. → half points
- No   = the subject of this criterion does not come up ANYWHERE in the recording, even in
         passing, even vaguely. You have searched the whole transcript and found nothing related.
         → 0 points

The two decisions are different questions. "No vs Half" asks: did this subject come up at all?
"Half vs Yes" asks: was every named element actually covered? Be generous on the first question
and strict on the second — if the criterion names five things and the officer got four, that is
Half, not Yes.
- MUST items (★): {must_str} — anything other than Yes on these = recording NOT ACCEPTED
{rubric_rules_block(rubric, client_version)}
{na_rule}

Before scoring any criterion No, ask yourself: "is there truly nothing anywhere in this
transcript about this subject?" If something related was said — however briefly — it is Half.

WORKED EXAMPLES of the distinctions that are most often got wrong:

  Criterion: "CURRENT OBLIGATIONS: every loan — lender, EMI, since when — reconciled against the
              bureau. Plus MFI/SHG, KCC, gold, hand loans."
  Recording: the officer gets both running loans with lender and EMI figures, reads the bureau
             entries back to the customer including an overdue and a guarantor entry, and asks
             about group, gold and hand loans.
  → Correct: Yes. WRONG: Half. Everything the criterion names was actually done, and the fact
    that you cannot open the bureau file yourself does not make it a failure.

  Criterion: "AGRICULTURE in full: land in whose name, acres, crops, yearly income, KCC running."
  Recording: 3 acres in his own name, paddy, ₹1.8 lakh last year — but KCC is never asked about.
  → Correct: Half (one named element missing). WRONG: Yes.

  Criterion: "Application ID, customer name, village/town, loan amount. CRIF and BPS checked."
  Recording: states the customer's name and the loan amount, but no application ID, no village,
             no CRIF/BPS.
  → Correct: Half (some elements covered, most missing). WRONG: No.

  Criterion: "WHICH PROPERTY: house/shop/plot, where, owner as per papers, which ORIGINAL papers
              — registry, rin pustika, patta, naksha."
  Recording: the plot is described and the owner named, but the officer never asks which original
             documents exist.
  → Correct: Half. WRONG: Yes.

  Criterion: "Told customer: house will be MORTGAGED, EMI amount, Clean Track Reward, ZERO
              COMMISSION — pay nobody."
  Recording: officer explains the property will be mortgaged and mentions the reward scheme, but
             never states an EMI figure.
  → Correct: Half. WRONG: No.

{absence_examples}

{render_profile(profile)}
CRITERIA (SECTION {section_id} — {section_name}):
{crit_list}

TRANSCRIPT — one line per utterance: "[MM:SS] speaker: words". The officer is the speaker asking
the questions (in a BCM self-audio recording he is usually the only speaker); the customer and family
answer. Use the speaker labels to tell who said what — a question the officer never asked cannot be
credited because the customer happened to mention the topic, and vice versa.
{transcript}

HONESTY RULES — these are non-negotiable and are checked afterwards:

1. NEVER state that something was said unless you can quote the words. Do not write a plausible
   note describing what "was discussed" when you could not find it. If it is not there, say so
   plainly: "not covered in the recording".
2. Your note MUST agree with your mark. Never describe something as absent or inadequate and
   then award Yes for it. If the note says a figure was never given, the mark cannot be Yes.
3. You have ONLY this transcript. You do NOT have the CAM, the PD report, the bureau/CIBIL file,
   the AA data or PRAGATI. Several criteria ask whether something matches those documents — you
   cannot check that. NEVER write that something is "consistent with the CAM", "within 10% of
   CAM" or "matches the bureau": that is inventing a check you did not perform.
   Equally, do NOT mark a criterion DOWN for the part you cannot verify. Judge only the part that
   is visible in the recording — whether the officer asked the question and what the customer
   answered — and note "CAM/bureau not available — not verified" for the comparison itself. A
   criterion is not a failure merely because you could not cross-check it against a document.
4. The quote you cite must be about the SAME subject as the element it proves. A quote that is
   genuinely from this recording but about something else is not evidence, and marks resting on it
   will be withdrawn.

HOW TO WORK EACH CRITERION — element by element. This is what makes the mark accurate:
a) Break the criterion into the separate things it requires — usually 2 to 6 "elements" — using its
   text and its "How to score" line. Leave out anything the "How to score" line says is NOT required,
   and anything that can only be checked against a document you do not have — "within 10% / 20% of
   the CAM", "consistent with the PD Report", "matches the bureau / AA" are NEVER elements.
   Where "How to score" says a criterion is scored on the officer ASKING a question, the element is
   "the officer asks ...", not the customer's answer.
b) For EACH element, search the WHOLE transcript — beginning, middle and end; long calls cover
   topics out of order and come back to them — and decide its status:
     "covered"       = said clearly, with the specifics (names, numbers, what was seen)
     "partial"       = the element comes up but stays vague or incomplete
     "stated_absent" = the recording clearly states this thing does NOT exist ("कोई ज़मीन नहीं है")
     "missing"       = nothing about this element anywhere in the recording
c) For every element that is not "missing", copy the words that prove it into "quote" — VERBATIM,
   character for character from the transcript, original Devanagari, 6-25 words, WITHOUT the
   "[MM:SS] speaker:" prefix — and put that line's time in "at". Every quote is looked up in the
   recording afterwards; a quote that cannot be found there counts as "missing", so copy exactly and
   never paraphrase, translate or invent.
d) Then give the mark: every element covered (or stated_absent where the criterion allows it) → Yes;
   at least one element covered or partial → Half; nothing → No. The system recomputes the mark
   from your elements after checking every quote.
e) Write every "note" and "element" in ENGLISH, even though the transcript is Hindi: for Yes, what was
   covered; for Half or No, exactly which elements were missing (max 160 chars).

Return ONLY valid JSON (no markdown fences), with exactly {n} items covering every criterion above:
{{
  "items": [
    {{
      "id": "{section_criteria[0]['id']}",
      "elements": [
        {{"element": "<one thing the criterion requires, in English>",
          "status": "covered|partial|stated_absent|missing",
          "quote": "<verbatim words from the transcript, or \"\" if missing>",
          "at": "<MM:SS of that line, or \"\">"}}
      ],
      "score": "Yes|Half|No",
      "note": "<in English: what was covered / exactly what was missing>"
    }}
  ]
}}"""


def build_recheck_prompt(transcript: str, no_criteria: list, absence_rule: str = None) -> str:
    """
    Second-pass recall check over criteria that came back "No".

    Scoring a section at a time still misses things: criteria listing 5-6
    elements get scored No when most elements are absent, even though the
    recording clearly covers one or two of them (crops named, the mortgage
    explained, the patta discussed, a court case explicitly ruled out). This
    pass looks at ONLY those criteria and asks the narrow question "is there
    anything at all about this in the recording?", which is far easier to
    answer correctly than a full scoring judgement.
    """
    crit_list = '\n'.join(f"  {c['id']}: {c['text']}" for c in no_criteria)
    if not absence_rule:
        return _legacy_recheck_prompt(transcript, no_criteria, crit_list)
    marked = [c['id'] for c in no_criteria if c.get('absence_counts')]
    return f"""You are auditing an evaluation of a Prodigee Finance loan-officer recording.

The criteria below were all marked as "not covered at all". Your ONLY job is to double-check that
— people reviewing this have found the "not covered" verdict is often wrong when the recording
actually did touch on the subject briefly.

For EACH criterion, search the whole transcript for ANYTHING related to its subject — however
brief, however incomplete, in Hindi or English. You are not judging how well it was covered; you
are only answering whether the subject came up at all.

If it did come up, copy the exact words from the transcript VERBATIM (10-25 words, original
Devanagari, no translation or paraphrase). If the subject genuinely never comes up anywhere, say
so — do not invent or stretch a quote to fit.

Also say whether those words are a CLEAR STATEMENT THAT THE THING DOES NOT EXIST for this
household (e.g. "कोई ज़मीन नहीं है", "कोई डिफ़ॉल्ट नहीं", "और कोई लोन नहीं चल रहा"). Under this
scorecard's rule — {absence_rule} — that matters for these criteria: {', '.join(marked) or 'none'}.
Set "stated_absence" true ONLY for such an explicit statement, never for a topic that is simply
not discussed.

CRITERIA TO RE-CHECK:
{crit_list}

TRANSCRIPT:
{transcript}

Return ONLY valid JSON (no markdown fences), one entry per criterion above:
{{
  "findings": [
    {{"id": "{no_criteria[0]['id']}", "found": true|false, "stated_absence": true|false,
      "evidence": "<exact words from the transcript, or \\"\\" if not found>"}}
  ]
}}"""


def _legacy_recheck_prompt(transcript: str, no_criteria: list, crit_list: str) -> str:
    return f"""You are auditing an evaluation of a Prodigee Finance loan-officer recording.

The criteria below were all marked as "not covered at all". Your ONLY job is to double-check that
— people reviewing this have found the "not covered" verdict is often wrong when the recording
actually did touch on the subject briefly.

For EACH criterion, search the whole transcript for ANYTHING related to its subject — however
brief, however incomplete, in Hindi or English. You are not judging how well it was covered; you
are only answering whether the subject came up at all.

If it did come up, copy the exact words from the transcript VERBATIM (10-25 words, original
Devanagari, no translation or paraphrase). If the subject genuinely never comes up anywhere, say
so — do not invent or stretch a quote to fit.

CRITERIA TO RE-CHECK:
{crit_list}

TRANSCRIPT:
{transcript}

Return ONLY valid JSON (no markdown fences), one entry per criterion above:
{{
  "findings": [
    {{"id": "{no_criteria[0]['id']}", "found": true|false,
      "evidence": "<exact words from the transcript, or \\"\\" if not found>"}}
  ]
}}"""


def recheck_no_items(transcript: str, segments: list, items: list, criteria: list,
                     chunk_size: int = 4, absence_rule: str = None) -> list:
    """
    Upgrade "No" items to "Half" where the recording does cover the subject.

    An upgrade requires a quote that VERIFIES against the diarised segments, so
    the model cannot talk an item up without real words behind it. Upgrades are
    capped at Half — this pass establishes that something was said, not that it
    was said well.

    The one exception is a scorecard with an absence rule (v1+): a verified,
    explicit statement that the thing does not exist ("no land") is full
    coverage by the client's own rule, so those criteria go to Yes.
    """
    cmap    = {c['id']: c for c in criteria}
    by_id   = {i.get('id'): i for i in items}
    no_ids  = [i['id'] for i in items if i.get('score') == 'No' and i['id'] in cmap]
    if not no_ids:
        return []

    upgraded = []
    for start in range(0, len(no_ids), chunk_size):
        batch = [cmap[i] for i in no_ids[start:start + chunk_size]]
        try:
            data = call_bedrock_json(build_recheck_prompt(transcript, batch, absence_rule),
                                     f"RECHECK-{start // chunk_size + 1}")
        except Exception as e:
            print(f"Recheck batch {start // chunk_size + 1} failed: {e} — leaving as scored")
            continue
        for f in data.get('findings', []):
            if not isinstance(f, dict) or not f.get('found'):
                continue
            item = by_id.get(f.get('id'))
            if not item or item.get('score') != 'No':
                continue
            quote = clean_quote(f.get('evidence'))
            ev = verify_evidence(quote, segments)
            if not ev['verified']:
                continue  # no verifiable words -> the "No" stands
            absent = (absence_rule and f.get('stated_absence') is True
                      and cmap[item['id']].get('absence_counts'))
            item['score']              = 'Yes' if absent else 'Half'
            item['evidence']           = quote
            item['evidence_timestamp'] = ev['timestamp']
            item['evidence_verified']  = True
            item['evidence_match']     = ev['match']
            item['evidence_speaker']   = ev['speaker']
            item['note'] = (f"Stated clearly that this does not exist (at {ev['timestamp']}) — "
                            f"counts as covered" if absent else
                            f"Partially covered (found on re-check at {ev['timestamp']})")
            item['upgraded_on_recheck'] = True
            # The re-check's quote replaces the element breakdown that found
            # nothing; it is then audited as a whole item.
            item['elements_before_recheck'] = item.pop('elements', None)
            upgraded.append(item['id'])
    if upgraded:
        marks = ', '.join(f"{i}->{by_id[i]['score']}" for i in upgraded)
        print(f"Recheck upgraded No items with verified evidence: {marks}")
    return upgraded


def build_relevance_audit_prompt(transcript: str, entries: list, absence_rule: str = None) -> str:
    """
    Audit that each credited item's quote actually evidences THAT criterion.

    Verifying a quote exists in the audio proves the words were spoken; it does
    not prove they have anything to do with the criterion they were attached
    to. The client's review found precisely this: one business-intro segment
    cited as the evidence for four unrelated items, a property item evidenced
    by the daily-income segment, and a "committed to NACH auto-debit" note on a
    call where NACH was never mentioned at all. Their instruction was to "make
    it mandatory that the quote contains the fact the item is about".
    """
    block = '\n'.join(
        f"  [{e['id']}]\n"
        f"    CRITERION: {e['text']}\n"
        f"    MARK GIVEN: {e['score']}\n"
        f"    QUOTE CITED: {e['evidence'] or '(none)'}"
        + ("\n    (a clear statement that this does NOT exist counts as covering it)"
           if absence_rule and e.get('absence_counts') else "")
        for e in entries)
    absence_note = ("\nUnder this scorecard, a clear statement that the thing does NOT exist (e.g. \"no land\", \"no\n"
                    "defaults\") DOES support the criteria marked so below — do not fail those quotes for\n"
                    "being about absence. For those criteria, before answering \"supports\": false, search the\n"
                    "transcript for such a statement (\"कोई ज़मीन नहीं\", \"कोई लोन नहीं\", \"कुछ नहीं है\") and,\n"
                    "if you find one, give it as \"better_evidence\".\n") if absence_rule else ""
    return f"""You are auditing the evidence behind an evaluation of a Prodigee Finance recording.

Each item below was awarded marks, with a quote cited as the proof. Your job is to check ONE
thing per item: does that quote actually show the thing the criterion is about?

A quote fails the check if it is about a different subject, even when it is genuinely from this
recording. Real examples of failures: a general "what is your business" exchange cited as proof
that the loan amount was asked; a daily-income segment cited as proof that the property was
described; a guarantor discussion cited as proof that original property papers were named.
{absence_note}
For each item answer:
- "supports": true if the quote genuinely evidences THIS criterion, false otherwise.
- If false, search the transcript for the words that DO evidence this criterion and put them in
  "better_evidence", copied VERBATIM (10-25 words, original script). If the recording genuinely
  contains nothing evidencing this criterion, set "better_evidence" to "" — do NOT stretch an
  unrelated quote to fit, and do not invent anything.

ITEMS TO AUDIT:
{block}

TRANSCRIPT:
{transcript}

Return ONLY valid JSON (no markdown fences), one entry per item above:
{{
  "audits": [
    {{"id": "{entries[0]['id']}", "supports": true|false, "better_evidence": "<verbatim quote or \\"\\">"}}
  ]
}}"""


def build_element_audit_prompt(transcript: str, entries: list) -> str:
    """
    Check each credited ELEMENT's quote against that one element.

    Auditing a whole criterion at once threw away good marks: one wrong quote
    among four correct ones got the entire criterion rejected (a production
    RCM call lost its fully discussed income items that way), while a real
    quote about the wrong subject slipped through on another item (loans in
    the father's name cited as proof the property was not mortgaged).
    """
    block = '\n'.join(
        f"  [{e['key']}] CRITERION {e['id']}: {e['text'][:220]}\n"
        f"      ELEMENT: {e['element']}\n"
        f"      QUOTE CITED: {e['quote']}"
        + ("\n      (a clear statement that this does NOT exist counts as proving it)"
           if e.get('absence_ok') else "")
        for e in entries)
    return f"""You are auditing the evidence behind an evaluation of a Prodigee Finance recording.

Each entry below is ONE element of a scoring criterion, with the quote cited as proof of that
element. Check each entry on its own: does THIS quote actually show THIS element?

A quote fails if it is about a different subject, even when it is genuinely from the recording —
e.g. loans in the father's name cited as proof the property is not mortgaged; "who is in your
family" cited as proof that documents were asked for.

For each entry answer:
- "supports": true if the quote genuinely proves this element, false otherwise.
- If false, search the WHOLE transcript for the words that DO prove this element and put them in
  "better_quote" — VERBATIM from the transcript, 6-25 words, without the "[MM:SS] speaker:"
  prefix. If nothing in the recording proves it, set "better_quote" to "". Never stretch an
  unrelated quote to fit and never invent words.

ENTRIES:
{block}

TRANSCRIPT (each line: "[MM:SS] speaker: words"):
{transcript}

Return ONLY valid JSON (no markdown fences), one entry per element above:
{{
  "audits": [
    {{"key": "{entries[0]['key']}", "supports": true|false, "better_quote": "<verbatim quote or \"\">"}}
  ]
}}"""


def audit_elements(transcript: str, segments: list, items: list, criteria: list,
                   chunk_size: int = 12) -> dict:
    """
    Element-level evidence audit for items scored element by element.

    Outcome per credited element: kept when its quote proves it; re-evidenced
    when the auditor finds a verifiable quote that does; otherwise marked
    missing. The criterion's mark is then recomputed from what survives, so a
    single bad quote costs only its own element.
    """
    cmap = {c['id']: c for c in criteria}
    stats = {'checked': 0, 'kept': 0, 'evidence_replaced': [], 'demoted': [], 'elements_dropped': 0}
    entries, where = [], {}
    for item in items:
        if item.get('id') not in cmap or not item.get('elements'):
            continue
        for n, e in enumerate(item['elements']):
            if e['status'] == 'missing' or not e.get('quote'):
                continue
            key = f"{item['id']}#{n + 1}"
            where[key] = (item, e)
            entries.append({'key': key, 'id': item['id'], 'text': cmap[item['id']]['text'],
                            'element': e['element'], 'quote': e['quote'],
                            'absence_ok': e['status'] == 'stated_absent'})
    before = {i['id']: i.get('score') for i in items}

    for start in range(0, len(entries), chunk_size):
        batch = entries[start:start + chunk_size]
        try:
            data = call_bedrock_json(build_element_audit_prompt(transcript, batch),
                                     f"EL-AUDIT-{start // chunk_size + 1}")
        except Exception as ex:
            print(f"Element audit batch {start // chunk_size + 1} failed: {ex} — keeping marks")
            continue
        for a in data.get('audits', []):
            if not isinstance(a, dict) or a.get('key') not in where:
                continue
            item, e = where[a['key']]
            stats['checked'] += 1
            if a.get('supports'):
                stats['kept'] += 1
                continue
            better = clean_quote(a.get('better_quote'))
            ev = verify_evidence(better, segments) if better else {'verified': False}
            if better and ev['verified'] and _long_enough(better):
                e['quote'], e['timestamp'] = better[:300], ev['timestamp']
                if item['id'] not in stats['evidence_replaced']:
                    stats['evidence_replaced'].append(item['id'])
            else:
                e['dropped_on_audit'] = e['quote']
                e['status'], e['quote'], e['timestamp'] = 'missing', '', ''
                stats['elements_dropped'] += 1

    for item in items:
        if item.get('id') in cmap and item.get('elements'):
            derive_from_elements(item)
            quote = item.get('evidence', '')
            ev = verify_evidence(quote, segments) if quote else \
                {'timestamp': '', 'verified': False, 'match': 'none', 'speaker': ''}
            item.update(evidence_timestamp=ev['timestamp'], evidence_verified=ev['verified'],
                        evidence_match=ev['match'], evidence_speaker=ev['speaker'])
            if _RANK_AUDIT.get(item['score'], 0) < _RANK_AUDIT.get(before.get(item['id']), 0):
                item['demoted_on_audit'] = True
                stats['demoted'].append(item['id'])
    if stats['evidence_replaced']:
        print(f"Element audit: re-evidenced {', '.join(stats['evidence_replaced'])}")
    if stats['demoted']:
        print(f"Element audit: lowered {', '.join(stats['demoted'])} "
              f"({stats['elements_dropped']} unsupported element(s) dropped)")
    return stats


_RANK_AUDIT = {'No': 0, 'N/A': 0, 'Half': 1, 'Yes': 2}


def audit_evidence_relevance(transcript: str, segments: list, items: list, criteria: list,
                             chunk_size: int = 6, absence_rule: str = None) -> dict:
    """
    Check every credited item's quote against its criterion.

    Outcome per item:
      - quote supports the criterion          -> left alone
      - a better, verifiable quote is found   -> evidence replaced, mark kept
      - nothing in the recording evidences it -> demoted to No, marks removed

    That last case is the important one: it is how a mark awarded on a
    fabricated or mis-attached justification loses its points instead of
    standing on a quote that does not back it up.
    """
    cmap  = {c['id']: c for c in criteria}
    stats = {'checked': 0, 'kept': 0, 'evidence_replaced': [], 'demoted': []}
    credited = [i for i in items
                if i.get('score') in ('Yes', 'Half') and i.get('id') in cmap
                and not i.get('elements')]
    if any(i.get('elements') for i in items):
        el = audit_elements(transcript, segments, items, criteria)
        for k in ('checked', 'kept'):
            stats[k] += el[k]
        stats['evidence_replaced'] += el['evidence_replaced']
        stats['demoted'] += el['demoted']
        stats['elements_dropped'] = el['elements_dropped']
    if not credited:
        return stats

    for start in range(0, len(credited), chunk_size):
        batch = credited[start:start + chunk_size]
        entries = [{'id': i['id'], 'text': cmap[i['id']]['text'],
                    'absence_counts': cmap[i['id']].get('absence_counts', False),
                    'score': i['score'], 'evidence': i.get('evidence', '')} for i in batch]
        try:
            data = call_bedrock_json(build_relevance_audit_prompt(transcript, entries, absence_rule),
                                     f"AUDIT-{start // chunk_size + 1}")
        except Exception as e:
            print(f"Relevance audit batch {start // chunk_size + 1} failed: {e} — keeping marks")
            continue
        by_id = {i['id']: i for i in batch}
        for a in data.get('audits', []):
            if not isinstance(a, dict):
                continue
            item = by_id.get(a.get('id'))
            if not item:
                continue
            stats['checked'] += 1
            if a.get('supports'):
                stats['kept'] += 1
                item['evidence_relevant'] = True
                continue
            better = clean_quote(a.get('better_evidence'))
            ev = verify_evidence(better, segments) if better else {'verified': False}
            if better and ev['verified']:
                item['evidence']           = better
                item['evidence_timestamp'] = ev['timestamp']
                item['evidence_verified']  = True
                item['evidence_match']     = ev['match']
                item['evidence_speaker']   = ev['speaker']
                item['evidence_relevant']  = True
                stats['evidence_replaced'].append(item['id'])
            else:
                # No verifiable words support the mark. A replacement "quote" that
                # cannot be found in the recording is not evidence either — one
                # production RCM call kept full marks on I2 with the criterion's
                # own wording pasted back as the "quote".
                if better:
                    stats.setdefault('unresolved', []).append(item['id'])
                # Step the mark DOWN ONE LEVEL rather than straight to zero.
                #
                # Two reasons. The client's own review kept marks whose evidence
                # was mis-cited but whose substance was real ("Mark OK, but
                # evidence and note are wrong"), so wiping the mark over a
                # citation fault overshoots. And a full Yes->No swing on one
                # model opinion is the single largest source of run-to-run
                # score movement — the same BM recording scored 36 and 22 on
                # consecutive runs, which is worse than being a few points off.
                # Keep what was cited, so a disputed demotion can be judged later.
                item['evidence_before_audit'] = item.get('evidence', '')
                item['score']              = 'Half' if item['score'] == 'Yes' else 'No'
                item['pts_scored']         = 0.0
                item['note']               = ('Evidence does not support this criterion — mark '
                                              'reduced on evidence audit; verify manually')
                item['evidence']           = ''
                item['evidence_timestamp'] = ''
                item['evidence_verified']  = False
                item['evidence_relevant']  = False
                item['demoted_on_audit']   = True
                stats['demoted'].append(item['id'])
    if stats['evidence_replaced']:
        print(f"Evidence audit: re-evidenced {', '.join(stats['evidence_replaced'])}")
    if stats['demoted']:
        print(f"Evidence audit: demoted (no supporting words) {', '.join(stats['demoted'])}")
    return stats


# Red-flag taxonomies live in the scorecard rubric (one per call type, each
# with its own codes and escalation action), so a scorecard version can extend
# them — e.g. v1 adds the BM warning signs for BT/takeover and coaching.


def build_red_flags_prompt(transcript: str, call_type: str, version: str = None) -> str:
    """
    Extract the red flags the call actually exposes.

    The client's review called this the bigger failure: "the evaluator only
    fills what has a cell, and right now red flags have no cell". On their RCM
    call the customer flatly denied a bureau overdue the officer had just read
    out, denied a guarantor entry, and a third party turned out to be arranging
    the file - none of it surfaced. So this runs as its own pass with its own
    output, rather than hoping a scoring criterion happens to mention it.
    """
    return f"""You are the risk reviewer for a Prodigee Finance {call_type} recording.

List the RED FLAGS this recording actually exposes. Look especially for:
- The customer DENYING something the officer has just read out from the file or bureau (an
  overdue, an enquiry, a guarantor entry, another loan). A denial contradicted on the call is
  itself a red flag.
- Loans, charges or mortgages on the property that surface anywhere in the conversation.
- A broker, agent, middleman or any third party arranging, funding or "doing" the file.
- Numbers that cannot be true together (household expenses far too low for the family size,
  EMI capacity that does not fit the stated income and existing EMIs).
- A written/physical record (diary, register, bill book) that states a bigger figure than what
  the officer's own on-the-ground count or observation actually supports (e.g. a milk diary
  showing 33-35 litres/day when only enough animals are present for ~12 litres/day) — flag this
  even when the officer already noticed and reported the mismatch themselves; do not drop it just
  because it was self-reported rather than hidden.
- The officer promising or implying sanction/approval while questions remain open.
- Reluctance about mortgaging, NACH auto-debit or insurance.

RED FLAG CODES:
{get_rubric(call_type, version).get("red_flags", "")}

Rules:
- Raise a flag ONLY on what is actually said in this recording. You do NOT have the CAM, bureau
  file or AA data, so never raise a flag that depends on comparing against a document you cannot
  see - unless the officer reads that document out loud on the call, which you may then use.
- Every flag needs a VERBATIM quote from the transcript (10-25 words, original script).
- If the recording genuinely exposes no red flags, return an empty list. Do not invent flags.

TRANSCRIPT:
{transcript}

Return ONLY valid JSON (no markdown fences):
{{
  "red_flags": [
    {{"code": "RF-6", "title": "<short label>",
      "detail": "<one sentence: what was said and why it is a risk>",
      "evidence": "<verbatim quote from the transcript>"}}
  ]
}}"""


def extract_red_flags(transcript: str, segments: list, call_type: str, version: str = None) -> list:
    """Red flags with verified timestamps; unverifiable quotes are dropped."""
    try:
        data = call_bedrock_json(build_red_flags_prompt(transcript, call_type, version), "RED-FLAGS")
    except Exception as e:
        print(f"Red flag extraction failed: {e}")
        return []
    flags = []
    for f in data.get('red_flags', []):
        if not isinstance(f, dict):
            continue
        quote = (f.get('evidence') or '').strip()
        ev = verify_evidence(quote, segments) if quote else {'timestamp': '', 'verified': False}
        # A flag we cannot point at in the audio is an allegation, not a finding.
        if not ev['verified']:
            print(f"Red flag {f.get('code')} dropped — evidence not found in recording")
            continue
        flags.append({
            'code':      str(f.get('code', 'RF-?'))[:8],
            'title':     str(f.get('title', ''))[:120],
            'detail':    str(f.get('detail', ''))[:400],
            'evidence':  quote[:300],
            'timestamp': ev['timestamp'],
        })
    print(f"Red flags raised: {[f['code'] for f in flags] or 'none'}")
    return flags


# Criteria that hinge on the officer actually SAYING a specific thing. The
# model keeps awarding these on atmosphere — the client's review caught it
# giving full marks for a recording-notice that was never spoken, and writing
# "committed to NACH auto-debit" on a call where NACH is never mentioned once.
# A phrase either occurs in the transcript or it does not, so this is settled
# in code instead of being argued with the model.
#
# Each rule caps the mark when NONE of the phrases appear anywhere. The cap is
# 'Half' where the utterance is one element among several in the criterion, so
# the officer keeps credit for the parts he did cover.
# Shared vocabularies, written as the SHORTEST DISTINCTIVE STEM rather than the
# full word.
#
# Amazon Transcribe mangles Hindi constantly — the RCM call says "आपकी प्रॉपर्टी
# गिरी हुई रखी जाएगी", where "गिरवी" (mortgaged) came out as "गिरी". Matching
# the full word would have missed it and wrongly capped a criterion the client
# had accepted.
#
# The asymmetry decides the design: over-matching only means we leave the mark
# to the model (safe), while under-matching invents a failure (harmful). So
# every list is deliberately loose — stems, common misrenderings, and both
# scripts.
_P_RECORDING = ['रिकॉर्ड', 'रिकार्ड', 'रेकॉर्ड', 'रेकार्ड', 'record']
_P_NACH      = ['नाच', 'नैच', 'नैक', 'ऑटो', 'auto', 'मैंडेट', 'mandate', 'ecs', 'ईसीएस',
                'डेबिट', 'debit']
_P_KCC       = ['केसीसी', 'के सी सी', 'किसान क्रेडिट', 'किसान कार्ड', 'kcc']
_P_PHOTO     = ['फोटो', 'फ़ोटो', 'तस्वीर', 'photo', 'अपलोड', 'upload', 'प्रगति', 'pragati',
                'खींच', 'खीच']
_P_AA        = ['एग्रीगेटर', 'aggregator', 'अकाउंट एग्री', 'एए चेक', 'aa check']
_P_NEIGHBOUR = ['पड़ोस', 'पडोस', 'पडौस', 'पड़ौस', 'नेबर', 'neighb', 'बगल', 'रेफरेंस',
                'reference', 'आसपास', 'आस पास']
_P_COMMISSION= ['कमीशन', 'commission', 'ब्रोकर', 'broker', 'डीएसए', 'dsa', 'एजेंट', 'agent',
                'दलाल', 'बिचौलिया', 'कनेक्टर']
_P_MORTGAGE  = ['गिर', 'मॉर्ट', 'मोर्ट', 'mortgage', 'बंधक', 'बन्धक', 'रेहन', 'रहन',
                'गहन', 'lap']
_P_CLEANTRACK= ['क्लीन', 'clean', 'रिवॉर्ड', 'रिवार्ड', 'reward', 'माफ']
_P_PAPERS    = ['डीड', 'deed', 'पट्टा', 'पट्टे', 'patta', 'रजिस्ट्री', 'registry', 'रिन पुस्तिका',
                'pustika', 'नक्शा', 'naksha', 'पेजर', 'pager', 'खसरा', 'नामांतरण', 'मुटेशन',
                'mutation', 'दस्तावेज', 'ओरिजिनल', 'original', 'कागज', 'पेपर', 'paper']
_P_DISPUTE   = ['विवाद', 'कोर्ट', 'court', 'मुकदमा', 'dispute', 'क्लियर', 'clear', 'चार्ज',
                'गिर', 'केस']
_P_STOCK     = ['स्टॉक', 'stock', 'माल', 'सामान', 'इन्वेंटरी', 'inventory', 'गिन', 'काउंट',
                'count']
_P_DECISION  = ['फॉरवर्ड', 'फारवर्ड', 'forward', 'आगे बढ़', 'आगे बड़', 'सीपीए', 'cpa',
                'रिजेक्ट', 'reject', 'अप्रूव', 'approve', 'मंजूर', 'क्रेडिट टीम', 'सैंक्शन',
                'sanction']
_P_NEXTSTEPS = ['सैंक्शन', 'sanction', 'केएफएस', 'kfs', 'की फैक्ट', 'डिस्बर्स', 'disburse',
                'नोडल', 'nodal', 'ग्रीवेंस', 'grievance', 'शिकायत']
# NB: no bare 'फी ' or 'fee' here — they match inside "स्क्वेयर फीट" / "feet"
# and would suppress a cap that should fire.
_P_LOGINFEE  = ['लॉगिन', 'लोगिन', 'लॉगइन', 'login', 'फीस', 'प्रोसेसिंग फी', 'processing fee']

# Criteria that hinge on the officer actually SAYING a specific thing. The model
# keeps awarding these on atmosphere — the client's review caught it giving full
# marks for a recording-notice that was never spoken, and writing "committed to
# NACH auto-debit" on a call where NACH is never mentioned once. A phrase either
# occurs in the transcript or it does not, so this is settled in code instead of
# being argued with the model.
#
# Every cap is to 'Half', never to 'No'. A corpus audit over 78 recordings
# showed the 'No' caps zeroing their criteria on EVERY recording (BCM A5/B7,
# BM G3, RCM I4 all sat at 0% credited) — a phrase missing from an imperfect
# Hindi transcript is not proof the officer did nothing, and Transcribe mangles
# these words routinely. The client's own corrections also used Half in every
# case where they judged from phrasing rather than content.
# Fresh loan vs balance transfer — v1 BM C4 / RCM B6. Loose on purpose, like
# the others: "ट्रांसफर" alone is enough to leave the mark to the model.
_P_BT        = ['बीटी', 'बी टी', 'ट्रांसफर', 'transfer', 'फ्रेश', 'fresh', 'टेकओवर', 'टेक ओवर',
                'takeover', 'take over', 'टॉप अप', 'टॉपअप', 'top up', 'top-up', 'नया लोन',
                'नई लोन', 'पुराना लोन', 'दूसरे बैंक', 'फोरक्लोज', 'foreclos', 'एफसीएल', 'fcl']

# Which phrase set a rubric's "required.phrases" name refers to.
PHRASE_SETS = {
    'RECORDING': _P_RECORDING, 'NACH': _P_NACH, 'KCC': _P_KCC, 'PHOTO': _P_PHOTO,
    'AA': _P_AA, 'NEIGHBOUR': _P_NEIGHBOUR, 'COMMISSION': _P_COMMISSION,
    'MORTGAGE': _P_MORTGAGE, 'CLEANTRACK': _P_CLEANTRACK, 'PAPERS': _P_PAPERS,
    'DISPUTE': _P_DISPUTE, 'STOCK': _P_STOCK, 'DECISION': _P_DECISION,
    'NEXTSTEPS': _P_NEXTSTEPS, 'LOGINFEE': _P_LOGINFEE, 'BT': _P_BT,
}


def required_utterances(call_type: str, version: str = None) -> dict:
    """{criterion id: {'phrases', 'cap', 'missing'}} for this scorecard version."""
    rules = {}
    for c in get_rubric(call_type, version)['criteria']:
        req = c.get('required')
        if req:
            rules[c['id']] = {'phrases': PHRASE_SETS[req['phrases']],
                              'cap': req.get('cap', 'Half'), 'missing': req['missing']}
    return rules


_RANK = {'No': 0, 'N/A': 0, 'Half': 1, 'Yes': 2}


def enforce_required_utterances(transcript: str, items: list, call_type: str,
                                version: str = None) -> list:
    """Cap marks for criteria whose required phrase never occurs in the recording."""
    rules = required_utterances(call_type, version)
    if not rules:
        return []
    haystack = _norm_text(transcript)
    by_id = {i.get('id'): i for i in items}
    capped = []
    for cid, rule in rules.items():
        item = by_id.get(cid)
        if not item:
            continue
        if any(_norm_text(p) in haystack for p in rule['phrases']):
            continue  # it was said — leave the mark alone
        if _RANK.get(item.get('score'), 0) <= _RANK[rule['cap']]:
            continue  # already at or below the cap
        item['score'] = rule['cap']
        # The existing note is the fabrication we are correcting ("committed to
        # NACH auto-debit" on a call with no NACH), so it is replaced outright
        # rather than kept alongside a contradicting mark.
        item['note'] = f"{rule['missing'][0].upper()}{rule['missing'][1:]} — marks capped."[:250]
        item['capped_on_required_phrase'] = True
        capped.append(cid)
    if capped:
        print(f"Required-phrase cap applied to: {', '.join(capped)}")
    return capped


def score_all_sections(scoring_transcript: str, segments: list, call_type: str, dur_secs,
                       profile: dict, version: str = None, raw_outputs: dict = None) -> tuple:
    """
    Score every criterion. Returns (items, failed_sections, defaulted_ids).

    One Bedrock call per section, or per batch of SCORING_BATCH criteria for a
    long section. Items the model does not return, or a batch that fails after
    retries, default to "No" and are reported so the run can be flagged.
    """
    criteria, _, section_names = get_config(call_type, version)
    all_items, failed_sections, defaulted_ids = [], [], []
    for sec_id in ordered_sections(criteria):
        batches = scoring_batches(criteria, sec_id)
        for n, batch in enumerate(batches, 1):
            label = f"SEC-{sec_id}" + (f"{n}" if len(batches) > 1 else "")
            key = f'section_{sec_id}' + (f'_{n}' if len(batches) > 1 else '')
            try:
                passes = []
                for p_no in range(1, max(1, SCORING_PASSES) + 1):
                    order = batch if p_no == 1 else list(reversed(batch))
                    sp = build_section_prompt(scoring_transcript, call_type, sec_id, order,
                                              section_names[sec_id], dur_secs, profile, version)
                    try:
                        sdata = call_bedrock_json(
                            sp, label if p_no == 1 else f"{label}-P{p_no}",
                            temperature=0.0 if p_no == 1 else SECOND_PASS_TEMPERATURE)
                    except Exception as e:
                        if p_no == 1:
                            raise
                        print(f"{label} pass {p_no} failed ({e}) — using pass 1")
                        continue
                    if raw_outputs is not None:
                        raw_outputs[key if p_no == 1 else f'{key}_pass{p_no}'] = sdata
                    # Only items that belong to this batch are kept — models
                    # occasionally echo ids from elsewhere or invent new ones.
                    items, missing = score_section_items(sdata, batch, segments)
                    for cid in missing:
                        items.append({'id': cid, 'score': 'No', 'pts_scored': 0,
                                      'note': 'Not returned by the evaluator — needs manual review',
                                      'defaulted': True})
                    order_ids = [c['id'] for c in batch]
                    passes.append(sorted(items, key=lambda i: order_ids.index(i['id'])))
                items = passes[0]
                for other in passes[1:]:
                    items = merge_passes(items, other)
                for i in items:
                    if i.get('defaulted'):
                        print(f"{label}: missing item {i['id']} — defaulting to No")
                        defaulted_ids.append(i['id'])
                all_items.extend(items)
            except Exception as e:
                print(f"{label} scoring failed after retries: {e} — defaulting to No")
                if sec_id not in failed_sections:
                    failed_sections.append(sec_id)
                for c in batch:
                    defaulted_ids.append(c['id'])
                    all_items.append({'id': c['id'], 'score': 'No', 'pts_scored': 0,
                                      'note': 'Evaluation error on this section — needs manual review',
                                      'defaulted': True})
    return all_items, failed_sections, defaulted_ids


def ordered_sections(criteria: list) -> list:
    """Section ids in first-occurrence order, e.g. ['A','B','C',...]."""
    seen = []
    for c in criteria:
        if c['sec'] not in seen:
            seen.append(c['sec'])
    return seen


def build_timestamped_transcript(segments: list, fallback: str = '') -> str:
    """
    Render the diarised segments as "[MM:SS] speaker: text" lines.

    The summary prompt asks for [MM:SS] against every figure, but it was being
    handed the flat transcript with no timing in it at all — so those stamps
    were invented. Giving it the real timings is what makes them true.
    """
    lines = []
    for s in segments:
        text = (s.get('text') or '').strip()
        if not text:
            continue
        lines.append(f"[{format_timestamp(s.get('start'))}] {s.get('speaker', 'spk')}: {text}")
    return '\n'.join(lines) if lines else fallback


def build_summary_prompt(transcript: str, eval_result: dict, call_type: str) -> str:
    items_compact = [{"id":i["id"],"score":i["score"],"note":i.get("note","")[:80]}
                     for i in eval_result.get("items",[])]
    return f"""You are the Audio Evaluation Agent for Prodigee Finance Limited.
Based on the transcript and completed item scores, write a Call Summary.
Call type: {call_type}

ITEMS SCORED: {json.dumps(items_compact)}
HEADER: {json.dumps(eval_result.get("header",{}))}

TRANSCRIPT (each line is prefixed with its real [MM:SS] position in the recording):
{transcript}

Rules:
1. Every figure must carry the [MM:SS] of the transcript line it came from. Copy that timestamp
   exactly as printed at the start of that line — never estimate, guess or invent one. If a figure
   has no line you can point to, write "not stated" for its timestamp.
2. Only use figures actually spoken on the recording.
3. Turnover ≠ income. Stated ≠ assessed unless verified.
4. executive_summary: 150-200 words covering borrower profile, income, key findings, recommendation.

Return ONLY valid JSON:
{{
  "executive_summary": "<150-200 words>",
  "income_streams": [
    {{"source":"<business/dairy/salary>","monthly_income":<number or null>,"proof":"<bills/UPI/nil>","timestamp":"[MM:SS]"}}
  ],
  "total_assessed_income": <number or null>,
  "obligations": [
    {{"lender":"<name>","type":"<EMI/gold/MFI>","emi":<number or null>,"overdue":"<yes/no/nil>"}}
  ],
  "total_emi": <number or null>,
  "family_expense": <number or null>,
  "surplus": <number or null>,
  "co_borrower_aware": "Yes|No|Not confirmed",
  "collateral_summary": "<type, owner, papers seen Y/N, approx value>",
  "red_flags": ["<RF description or empty list>"],
  "recommendation": "<exact words from recording or Not stated>",
  "evaluators_note": "<gaps for management to act on>"
}}"""


# ── Score calculation ─────────────────────────────────────────────────────────
#
# This MUST stay identical to the client's Excel template formulas, because the
# scorecard recomputes everything live when they open it and a mismatch between
# the sheet and our UI/DynamoDB number is indefensible. Per the templates:
#
#   Points scored     = SUM(F)          where F = Yes -> pts, Half -> pts/2, else 0
#   Points applicable = 100 (v1: always)
#                       legacy BCM only: 100 - IF(E6="No",10,0)   (section C)
#   SCORE             = ROUND(scored / applicable * 100, 0)        <- 0 decimals
#   MUST missed       = every MUST item whose mark is not exactly "Yes"
#
# Note there is no per-item N/A in the sheet: an "N/A" mark simply scores zero
# while still counting in the denominator, so we never emit N/A as a mark.
def calculate_scores(eval_result: dict, call_type: str, other_income: bool = True,
                     version: str = None) -> dict:
    rubric = get_rubric(call_type, version)
    criteria, section_pts, section_names = get_config(call_type, version)
    crit_map = {c['id']: c for c in criteria}
    items_by_id = {i['id']: i for i in eval_result.get('items', [])}

    # Legacy BCM templates dropped section C from the denominator when the case
    # had no farm/dairy/other family income (cell E6). From v1 section C is
    # always scored — a clearly stated absence earns the points instead.
    drop_section_c = (rubric.get('applicable_rule') == 'bcm_drop_c_without_other_income'
                      and not other_income)

    sections = {}
    for c in criteria:
        sid = c['sec']
        if sid not in sections:
            sections[sid] = {'name': section_names[sid], 'max': 0, 'applicable': 0, 'scored': 0.0}
        item   = items_by_id.get(c['id'], {})
        score  = item.get('score', 'No')
        pts    = c['pts']
        sections[sid]['max'] += pts
        excluded = drop_section_c and sid == 'C'
        if not excluded:
            sections[sid]['applicable'] += pts
            if score == 'Yes':
                sections[sid]['scored'] += pts
            elif score == 'Half':
                sections[sid]['scored'] += pts / 2.0
        item['pts_scored'] = 0.0 if excluded else (
            pts if score == 'Yes' else (pts / 2.0 if score == 'Half' else 0.0))

    total_applicable = sum(s['applicable'] for s in sections.values())
    total_scored     = sum(s['scored']     for s in sections.values())
    total_score      = float(round(total_scored / total_applicable * 100)) if total_applicable > 0 else 0.0

    # MUST items check — template counts anything that is not exactly "Yes".
    must_failures = []
    for c in criteria:
        if c['must']:
            item = items_by_id.get(c['id'], {})
            s    = item.get('score', 'No')
            if s != 'Yes':
                must_failures.append({'id': c['id'], 'score': s, 'note': item.get('note', '')})

    # Duration validity check
    # A fixed grace window is applied around the nominal ranges below — real
    # calls routinely run a couple of minutes short/long of the target band
    # (e.g. a BM call at 15:36 is still a valid genuineness check, not an
    # invalid recording) and a hard cutoff was rejecting otherwise-good calls
    # over trivial overruns.
    dur_secs = eval_result.get('_duration_secs', None)
    duration_item = items_by_id.get('A3', items_by_id.get('A1', {}))

    GRACE_SECS = 3 * 60
    min_dur, max_dur = (m * 60 for m in rubric['duration_minutes'])

    recording_invalid = False
    if dur_secs is not None:
        if dur_secs < max(0, min_dur - GRACE_SECS) or dur_secs > max_dur + GRACE_SECS:
            recording_invalid = True

    # Verdict
    if recording_invalid:
        verdict = 'NOT ACCEPTED — Recording duration invalid'
        grade   = 'NOT ACCEPTED'
    elif must_failures:
        missed = ', '.join(f['id'] for f in must_failures)
        verdict = f'NOT ACCEPTED — MUST items not fully covered: {missed}'
        grade   = 'NOT ACCEPTED'
    elif total_score >= 90:
        verdict = 'EXCELLENT — Model recording'
        grade   = 'EXCELLENT'
    elif total_score >= 75:
        verdict = 'ACCEPTED'
        grade   = 'ACCEPTED'
    else:
        verdict = 'NOT ACCEPTED — Score below 75'
        grade   = 'NOT ACCEPTED'

    # Top 3 gaps (most points lost)
    gaps = []
    for c in criteria:
        item  = items_by_id.get(c['id'], {})
        score = item.get('score', 'No')
        if score in ('No', 'Half'):
            lost = c['pts'] if score == 'No' else c['pts'] / 2.0
            gaps.append({'id': c['id'], 'text': c['text'][:80], 'pts_lost': lost, 'score': score})
    gaps.sort(key=lambda x: -x['pts_lost'])
    top3_gaps = gaps[:3]

    # Section comments
    for sid, s in sections.items():
        pct = round(s['scored'] / s['applicable'] * 100) if s['applicable'] > 0 else 0
        s['pct'] = pct
        if pct == 100:   s['comment'] = 'Fully covered'
        elif pct >= 80:  s['comment'] = 'Good — small gaps'
        elif pct >= 50:  s['comment'] = 'Weak — see items below'
        else:            s['comment'] = 'Mostly missed'

    return {
        'total_score':       Decimal(str(total_score)),
        'total_scored':      float(total_scored),
        'total_applicable':  float(total_applicable),
        'grade':             grade,
        'verdict':           verdict,
        'sections':          sections,
        'must_failures':     must_failures,
        'recording_invalid': recording_invalid,
        'top3_gaps':         top3_gaps,
        'all_gaps':          gaps,
        'call_type':         call_type,
        'scorecard_version': version or SCORECARD_VERSION,
    }


# ── Main Lambda handler ───────────────────────────────────────────────────────
def lambda_handler(event, context):
    print(f"Evaluation request: {json.dumps(event)}")
    try:
        evaluation_id  = event['evaluation_id']
        application_id = event['application_id']
        manual_ct      = event.get('call_type', 'AUTO_DETECT')
        transcript_key = event['transcript_s3_key']
        created_at     = event.get('created_at')
        dur_secs       = event.get('audio_duration_seconds')
        # New evaluations use the current scorecard; a reprocess may pin one.
        version        = str(event.get('scorecard_version') or SCORECARD_VERSION)
        scorecards.load(version)          # fail fast on an unknown version
        version_label  = scorecards.label(version)
        print(f"Scorecard version: {version} ({version_label})")

        table = dynamodb.Table(DYNAMODB_TABLE)
        table.update_item(
            Key={'evaluation_id': evaluation_id, 'created_at': created_at},
            UpdateExpression='SET #s = :s, evaluation_started_at = :t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'EVALUATING', ':t': datetime.utcnow().isoformat()}
        )

        # Load transcript
        raw  = s3_client.get_object(Bucket=TRANSCRIPTS_BUCKET, Key=transcript_key)
        tdata = json.loads(raw['Body'].read())
        transcript = tdata.get('transcript', '')
        print(f"Transcript: {len(transcript)} chars")

        segments = tdata.get('segments', [])
        speakers = set(s.get('speaker') for s in segments if s.get('speaker'))
        speaker_count = len(speakers)
        print(f"Speakers: {speaker_count} — {speakers}")

        # ── Gate: a transcript this short means silent/unintelligible audio, not
        # a bad recording. Scoring it would produce a meaningless 0/100 that
        # looks like a real verdict, so fail loudly instead.
        if len(transcript.strip()) < MIN_TRANSCRIPT_CHARS:
            msg = (f"Transcript too short to evaluate ({len(transcript.strip())} chars). "
                   f"The audio is likely silent, corrupted or unintelligible.")
            print(f"ABORT: {msg}")
            table.update_item(
                Key={'evaluation_id': evaluation_id, 'created_at': created_at},
                UpdateExpression='SET #s=:s, error_message=:e',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'FAILED', ':e': msg}
            )
            return {'statusCode': 422, 'body': json.dumps({'error': msg,
                                                           'evaluation_id': evaluation_id})}

        raw_outputs = {}
        review_reasons = []

        classification = classify_call_type(transcript, speaker_count, manual_ct, dur_secs)
        call_type = classification['call_type']
        raw_outputs['classification'] = classification
        print(f"Call type: {call_type} (confidence={classification['confidence']}, "
              f"method={classification['method']})")
        if classification['confidence'] in ('low', 'unknown'):
            review_reasons.append(
                f"Call type detected with {classification['confidence']} confidence "
                f"via {classification['method']} — verify the rubric applied is correct")

        # ── BCM must be a self-audio narration, not an interview ─────────────
        # Scoring an interview against the BCM checklist produces an
        # authoritative-looking scorecard that means nothing, which is exactly
        # what the client rejected. Refuse instead.
        self_audio = None
        if call_type == 'BCM_PHYSICAL_PD':
            self_audio = check_bcm_self_audio(transcript, segments)
            raw_outputs['self_audio_check'] = self_audio
            if self_audio.get('overridden') == 'no_qa_evidence':
                review_reasons.append(
                    'The self-audio check called this an interview, but its evidence was not a '
                    'question-and-answer exchange — evaluated as a BCM self-audio report; verify')
            if not self_audio['is_self_audio'] and self_audio['confidence'] in ('high', 'medium'):
                verdict = (
                    'NOT EVALUABLE — a BCM Physical PD submission must be a self-audio recording '
                    'in which the BCM narrates his own observations after the visit. This '
                    'recording is an interview with the customer (questions and answers), so the '
                    'BCM checklist ("what you SAW", "your view", "photos you took") cannot be '
                    'marked against it. Please submit the BCM self-audio observation report.')
                print(f"ABORT (BCM not self-audio): {self_audio['reason']}")
                result_key = f"evaluations/{evaluation_id}/evaluation-result.json"
                s3_client.put_object(
                    Bucket=REPORTS_BUCKET, Key=result_key,
                    Body=json.dumps({
                        'evaluation_id': evaluation_id, 'application_id': application_id,
                        'call_type': call_type, 'evaluated_at': datetime.utcnow().isoformat(),
                        'scorecard_version': version, 'scorecard_label': version_label,
                        'duration_seconds': dur_secs, 'not_evaluable': True,
                        'recording_kind': self_audio['kind'],
                        'verdict': verdict, 'self_audio_check': self_audio,
                        'items': [], 'scoring': {},
                    }, indent=2, cls=DecimalEncoder, ensure_ascii=False),
                    ContentType='application/json')
                table.update_item(
                    Key={'evaluation_id': evaluation_id, 'created_at': created_at},
                    # excel_s3_key is REMOVEd so a scorecard from an earlier run
                    # cannot still be downloaded for a recording we just refused
                    # to evaluate.
                    UpdateExpression='SET #s=:s, grade_band=:g, verdict=:v, evaluated_at=:t, '
                                     'result_s3_key=:k, call_type=:ct, needs_review=:nr, '
                                     'review_reasons=:rr, not_evaluable=:ne, total_score=:sc, '
                                     'scorecard_version=:sv '
                                     'REMOVE excel_s3_key',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={
                        ':s': 'COMPLETED', ':g': 'NOT EVALUABLE', ':v': verdict,
                        ':t': datetime.utcnow().isoformat(), ':k': result_key, ':ct': call_type,
                        ':nr': True, ':ne': True,
                        ':rr': [f"Recording is an interview, not a BCM self-audio report "
                                f"({self_audio['reason']})"],
                        ':sc': Decimal('0'), ':sv': version,
                    })
                return {'statusCode': 200, 'body': json.dumps({
                    'evaluation_id': evaluation_id, 'not_evaluable': True,
                    'recording_kind': self_audio['kind'], 'verdict': verdict})}

        rubric = get_rubric(call_type, version)
        criteria, section_pts, section_names = get_config(call_type, version)
        absence_rule = rubric.get('absence_rule')
        # The scoring passes read "[MM:SS] speaker: words" lines, so the model
        # can tell who said what and quote the right moment of a long call.
        scoring_transcript = build_timestamped_transcript(segments, transcript)

        # Header extraction — one small dedicated call
        try:
            hp = build_header_prompt(transcript, call_type, dur_secs, version)
            header = call_bedrock_json(hp, "HEADER")
            raw_outputs['header'] = header
        except Exception as e:
            print(f"Header extraction failed: {e} — using empty header")
            header = {}
            review_reasons.append("Header details could not be extracted")

        # Borrower profile — decides which criteria are applicable at all, so
        # that e.g. a salaried borrower with no shop is not marked down on the
        # whole Business section (see build_profile_prompt).
        try:
            profile = call_bedrock_json(build_profile_prompt(transcript, call_type), "PROFILE")
            raw_outputs['profile'] = profile
            print(f"Borrower profile: {json.dumps(profile, ensure_ascii=False)}")
        except Exception as e:
            print(f"Profile extraction failed: {e} — scoring without applicability hints")
            profile = {}

        # Item scoring — one call per section, or per batch of a long section
        # (see build_section_prompt), each criterion worked element by element
        # against a timestamped, speaker-labelled transcript.
        all_items, failed_sections, defaulted_ids = score_all_sections(
            scoring_transcript, segments, call_type, dur_secs, profile, version, raw_outputs)

        # Post-passes: find coverage the section pass missed, then withdraw any
        # marks whose evidence does not actually support the criterion.
        recheck_upgrades = recheck_no_items(scoring_transcript, segments, all_items, criteria,
                                            absence_rule=absence_rule)
        audit_stats      = audit_evidence_relevance(scoring_transcript, segments, all_items, criteria,
                                                    absence_rule=absence_rule)
        capped_items     = enforce_required_utterances(transcript, all_items, call_type, version)
        red_flags        = extract_red_flags(transcript, segments, call_type, version)
        raw_outputs['red_flags'] = red_flags

        r1 = {'call_type_detected': call_type, 'header': header, 'items': all_items,
              '_duration_secs': dur_secs}
        print(f"Items scored: {len(all_items)} | defaulted: {len(defaulted_ids)} | "
              f"failed sections: {failed_sections or 'none'}")

        # ── A score computed from defaults is not a score ────────────────────
        # When sections fail (Bedrock throttling, most often) every criterion in
        # them defaults to "No", and publishing that produced 0/100 scorecards
        # that looked like real verdicts on perfectly good recordings — the
        # single most misleading thing this pipeline has done. If too much of
        # the rubric never actually got evaluated, fail loudly and let it be
        # retried instead of inventing a number.
        default_share = len(defaulted_ids) / max(len(criteria), 1)
        if default_share > MAX_DEFAULTED_SHARE:
            msg = (f"Evaluation incomplete — {len(defaulted_ids)} of {len(criteria)} criteria "
                   f"could not be evaluated"
                   + (f" (sections {', '.join(failed_sections)} failed)" if failed_sections else "")
                   + ". No score published; please retry.")
            print(f"ABORT: {msg}")
            # Record the failure but LEAVE any previous score in place. Removing
            # it blanked the row in the history and disabled its download — a
            # failed retry must not destroy the last good evaluation.
            table.update_item(
                Key={'evaluation_id': evaluation_id, 'created_at': created_at},
                UpdateExpression='SET #s=:s, error_message=:e, needs_review=:nr',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'FAILED', ':e': msg, ':nr': True})
            return {'statusCode': 503, 'body': json.dumps({'error': msg,
                                                           'evaluation_id': evaluation_id,
                                                           'retryable': True})}

        # Call 2: Summary — fed the timestamped transcript so the [MM:SS]
        # stamps it attaches to figures are real rather than invented.
        try:
            p2 = build_summary_prompt(
                build_timestamped_transcript(segments, transcript), r1, call_type)
            r2 = call_bedrock_json(p2, "SUMMARY")
            raw_outputs['summary'] = r2
        except Exception as e:
            print(f"Summary generation failed: {e} — using empty summary")
            r2 = {}
            review_reasons.append("Call summary could not be generated")

        # Score calculation. other_income fills the BCM sheet's E6 cell; on
        # legacy scorecards it also dropped section C from the denominator.
        other_income = has_other_income(profile)
        scoring = calculate_scores(r1, call_type, other_income=other_income, version=version)
        scoring['other_income'] = other_income
        print(f"Score: {scoring['total_score']} | {scoring['grade']}")

        # ── Integrity checks ─────────────────────────────────────────────────
        # A scorecard where nothing at all was credited is the exact signature
        # of an evaluator failure (the bug that produced 0/100 scorecards from
        # perfectly good recordings). A genuinely worthless recording is
        # possible but rare — either way a human should look before this is
        # treated as a real verdict.
        if failed_sections:
            review_reasons.append(
                f"Section(s) {', '.join(failed_sections)} could not be evaluated and were "
                f"scored 0 by default")
        elif defaulted_ids:
            review_reasons.append(
                f"{len(defaulted_ids)} criteria were not returned by the evaluator and "
                f"defaulted to 0: {', '.join(defaulted_ids[:10])}"
                + ("..." if len(defaulted_ids) > 10 else ""))

        scored_items = [i for i in all_items if i.get('score') in ('Yes', 'Half')]
        if not scored_items:
            review_reasons.append(
                "No criterion scored above zero — this usually indicates an evaluator "
                "failure rather than a genuinely empty recording")

        # Evidence integrity: every criterion that earned points should be
        # traceable to real words in the recording. Points awarded without a
        # locatable quote are the signature of a hallucinated score, which is
        # exactly what the evidence trail exists to catch.
        unverified = [i['id'] for i in scored_items if not i.get('evidence_verified')]
        evidence_stats = {
            'credited_items':   len(scored_items),
            'with_evidence':    len(scored_items) - len(unverified),
            'unverified_items': unverified,
        }
        print(f"Evidence: {evidence_stats['with_evidence']}/{len(scored_items)} credited items "
              f"traced to the recording")
        if scored_items and len(unverified) > len(scored_items) * 0.4:
            review_reasons.append(
                f"{len(unverified)} of {len(scored_items)} credited criteria could not be traced "
                f"to words in the recording ({', '.join(unverified[:10])}"
                + ("..." if len(unverified) > 10 else "") + ") — scores may not be evidence-backed")

        if audit_stats.get('demoted'):
            review_reasons.append(
                f"{len(audit_stats['demoted'])} item(s) had marks withdrawn because nothing in "
                f"the recording evidenced them: {', '.join(audit_stats['demoted'])}")

        needs_review = bool(review_reasons)
        if needs_review:
            print(f"NEEDS REVIEW: {review_reasons}")

        # Build result
        result = {
            'evaluation_id': evaluation_id,
            'application_id': application_id,
            'call_type': call_type,
            'call_type_confidence': classification['confidence'],
            'call_type_reason': classification['reason'],
            'call_type_method': classification['method'],
            'evaluated_at': datetime.utcnow().isoformat(),
            'scorecard_version': version,
            'scorecard_label': version_label,
            'evaluator_model': BEDROCK_MODEL_ID,
            'duration_seconds': dur_secs,
            'speaker_count': speaker_count,
            'transcript_chars': len(transcript),
            'needs_review': needs_review,
            'review_reasons': review_reasons,
            'evidence_stats': evidence_stats,
            'evidence_audit': audit_stats,
            'red_flags': red_flags,
            'other_income': other_income,
            'recording_kind': (self_audio or {}).get('kind', 'n/a'),
            'header': r1.get('header', {}),
            'items': r1.get('items', []),
            'summary': r2,
            'scoring': scoring,
        }

        # Save to S3
        result_key = f"evaluations/{evaluation_id}/evaluation-result.json"
        s3_client.put_object(
            Bucket=REPORTS_BUCKET, Key=result_key,
            Body=json.dumps(result, indent=2, cls=DecimalEncoder),
            ContentType='application/json'
        )

        # Save the raw model outputs alongside it — makes a disputed or odd
        # scorecard debuggable later without re-running the whole pipeline.
        try:
            s3_client.put_object(
                Bucket=REPORTS_BUCKET,
                Key=f"evaluations/{evaluation_id}/raw-model-outputs.json",
                Body=json.dumps(raw_outputs, indent=2, ensure_ascii=False, cls=DecimalEncoder),
                ContentType='application/json'
            )
        except Exception as ex:
            print(f"Raw output save failed (non-fatal): {ex}")

        # Update DynamoDB
        table.update_item(
            Key={'evaluation_id': evaluation_id, 'created_at': created_at},
            UpdateExpression='SET #s=:s, total_score=:sc, grade_band=:g, verdict=:v, '
                             'evaluated_at=:t, result_s3_key=:k, call_type=:ct, '
                             'call_type_confidence=:ctc, needs_review=:nr, review_reasons=:rr, '
                             'scorecard_version=:sv',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':s': 'COMPLETED', ':sc': scoring['total_score'],
                ':g': scoring['grade'], ':v': scoring['verdict'],
                ':t': datetime.utcnow().isoformat(),
                ':k': result_key, ':ct': call_type,
                ':ctc': classification['confidence'],
                ':nr': needs_review, ':rr': review_reasons, ':sv': version,
            }
        )

        # Trigger Excel
        try:
            lc = boto3.client('lambda')
            lc.invoke(
                FunctionName=os.environ.get('EXCEL_GENERATOR_LAMBDA_ARN'),
                InvocationType='Event',
                Payload=json.dumps({'evaluation_id': evaluation_id,
                                    'result_s3_key': result_key,
                                    'created_at': created_at,
                                    'call_type': call_type,
                                    'scorecard_version': version}).encode()
            )
            print("Excel triggered")
        except Exception as ex:
            print(f"Excel trigger failed (non-fatal): {ex}")

        return {'statusCode': 200, 'body': json.dumps({
            'evaluation_id': evaluation_id,
            'score': float(scoring['total_score']),
            'grade': scoring['grade'],
            'call_type': call_type,
            'scorecard_version': version,
            'call_type_confidence': classification['confidence'],
            'needs_review': needs_review,
            'review_reasons': review_reasons,
        })}

    except Exception as e:
        print(f"Evaluation error: {e}")
        import traceback; traceback.print_exc()
        try:
            dynamodb.Table(DYNAMODB_TABLE).update_item(
                Key={'evaluation_id': event['evaluation_id'], 'created_at': event.get('created_at')},
                UpdateExpression='SET #s=:s, error_message=:e',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'FAILED', ':e': str(e)}
            )
        except Exception: pass
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
