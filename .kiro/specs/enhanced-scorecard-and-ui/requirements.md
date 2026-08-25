# Requirements Document

## Introduction

The Audio PD Evaluation System currently provides basic Excel scorecard generation and upload functionality, but lacks comprehensive data population in Excel files and real-time status tracking in the frontend. This enhancement addresses two critical gaps:

1. **Complete Excel Scorecard Generation**: The Excel generator Lambda currently only fills basic header fields but needs to populate the complete scorecard with all 37 criteria ratings, evidence with timestamps, 10 section subtotals, 9 knockout gate results, total scores, grade bands, and red flags based on AI evaluation results.

2. **Frontend Status Tracking and History**: The frontend needs real-time status tracking through the entire processing pipeline (upload → transcription → evaluation → Excel generation), post-completion action buttons for viewing transcripts and downloading results, and a comprehensive history view of all previous evaluations with the ability to access results without re-uploading.

This feature will provide users with complete, evidence-backed scorecards and a seamless experience for tracking and accessing evaluation results.

## Glossary

- **Excel_Generator_Lambda**: AWS Lambda function that reads the AI evaluation JSON and fills the Excel template scorecard
- **Frontend**: React + TypeScript + Vite web application hosted on S3/CloudFront
- **AI_Evaluation_Result**: JSON output from the evaluation Lambda containing detailed scores, evidence, gates, and red flags for all 37 criteria
- **Scorecard_Template**: Excel template file stored in S3 at `templates/prodegee_template.xlsx` with predefined structure
- **DynamoDB_Evaluation_Table**: Database table storing evaluation metadata including status, scores, and S3 keys
- **Criterion**: Individual evaluation item (37 total: A1-A5, B1-B5, C1-C3, D1-D6, E1-E4, F1-F3, G1-G3, H1-H3, I1-I4, J1-J3)
- **Section**: Grouping of criteria (10 total: A through J) with subtotal calculations
- **Knockout_Gate**: Binary pass/fail gate (9 total: K1-K9) that can invalidate an evaluation
- **Coverage_Rating**: Score for each criterion (Full / Partial / None / N/A)
- **Evidence**: Timestamped transcript excerpt supporting each criterion rating
- **Section_Subtotal**: Aggregated score for a section with coverage percentage
- **Grade_Band**: Letter grade (A/B/C/D) and label (Excellent/Pass/Conditional/Fail) based on total score
- **Red_Flag**: Critical issue flagged during evaluation (RF-1 through RF-10)
- **Status_Pipeline**: Processing stages (UPLOADED → PREPROCESSING → TRANSCRIBING → EVALUATING → EXCEL_GENERATING → COMPLETED)
- **History_Tab**: Frontend page showing all previous evaluations with filtering and action capabilities
- **Polling_Service**: Frontend service that periodically checks DynamoDB for status updates
- **Transcript_Viewer**: Frontend component displaying the full call transcript
- **Excel_Preview**: Frontend component for viewing Excel content online before download

## Requirements

### Requirement 1: Complete Excel Scorecard Header Population

**User Story:** As a reviewer, I want the Excel scorecard header section to be completely filled with evaluation metadata, so that I can identify the case and understand evaluation context at a glance.

#### Acceptance Criteria

1. WHEN the Excel_Generator_Lambda processes an evaluation result, THE Excel_Generator_Lambda SHALL populate cell B3 with the application_id from AI_Evaluation_Result
2. WHEN the Excel_Generator_Lambda processes an evaluation result, THE Excel_Generator_Lambda SHALL populate cell E3 with the evaluator name "AI Agent (Claude Haiku 4.5)"
3. WHEN the Excel_Generator_Lambda processes an evaluation result, THE Excel_Generator_Lambda SHALL populate cell B4 with customer_name from AI_Evaluation_Result metadata
4. WHEN the Excel_Generator_Lambda processes an evaluation result, THE Excel_Generator_Lambda SHALL populate cell B5 with RCM name extracted from transcript or default to "As per recording"
5. WHEN the Excel_Generator_Lambda processes an evaluation result, THE Excel_Generator_Lambda SHALL populate cell B6 with evaluated_at timestamp formatted as DD-MMM-YYYY
6. WHEN the Excel_Generator_Lambda processes an evaluation result, THE Excel_Generator_Lambda SHALL populate cell B7 with recording duration calculated from transcript segments or default to "N/A"
7. WHEN the Excel_Generator_Lambda processes an evaluation result, THE Excel_Generator_Lambda SHALL populate cell B8 with loan_amount from case_context or default to "N/A"

### Requirement 2: Complete Criteria Coverage and Evidence Population

**User Story:** As a reviewer, I want all 37 criteria rows filled with coverage ratings and timestamped evidence, so that I can verify AI evaluation decisions with transcript references.

#### Acceptance Criteria

1. FOR ALL 37 criteria (A1-A5, B1-B5, C1-C3, D1-D6, E1-E4, F1-F3, G1-G3, H1-H3, I1-I4, J1-J3), THE Excel_Generator_Lambda SHALL map criterion ID to the correct Excel row (A1→row 12, A2→row 13, ..., J3→row 59)
2. WHEN a Criterion has coverage rating in AI_Evaluation_Result, THE Excel_Generator_Lambda SHALL populate column G of the criterion row with the coverage value (Full / Partial / None / N/A)
3. WHEN a Criterion has evidence in AI_Evaluation_Result, THE Excel_Generator_Lambda SHALL populate column I of the criterion row with evidence text including timestamps
4. WHEN a Criterion has coverage "N/A", THE Excel_Generator_Lambda SHALL exclude the criterion from points calculation and mark column H as "N/A"
5. WHEN a Criterion has coverage "None", THE Excel_Generator_Lambda SHALL populate column H with 0 points
6. WHEN a Criterion has coverage "Partial", THE Excel_Generator_Lambda SHALL populate column H with points_scored from AI_Evaluation_Result
7. WHEN a Criterion has coverage "Full", THE Excel_Generator_Lambda SHALL populate column H with max_points from the criterion definition

### Requirement 3: Section Subtotals and Coverage Percentage Population

**User Story:** As a reviewer, I want section-wise subtotals with coverage percentages in the Excel scorecard, so that I can quickly identify strong and weak evaluation areas.

#### Acceptance Criteria

1. FOR ALL 10 sections (A through J), THE Excel_Generator_Lambda SHALL calculate applicable_points as the sum of max_points for all criteria where coverage is not "N/A"
2. FOR ALL 10 sections (A through J), THE Excel_Generator_Lambda SHALL calculate scored_points as the sum of points_scored for all criteria in the section
3. FOR ALL 10 sections (A through J), THE Excel_Generator_Lambda SHALL calculate coverage_percentage as (scored_points / applicable_points) × 100
4. WHEN Section_Subtotal is calculated, THE Excel_Generator_Lambda SHALL populate the section subtotal row with scored_points, applicable_points, and coverage_percentage
5. WHEN applicable_points for a section equals zero, THE Excel_Generator_Lambda SHALL set coverage_percentage to 0 and mark the section as "N/A"

### Requirement 4: Knockout Gate Status Population

**User Story:** As a reviewer, I want all 9 knockout gate results clearly marked as PASS or FAIL in the Excel scorecard, so that I can immediately identify evaluation-invalidating issues.

#### Acceptance Criteria

1. FOR ALL 9 Knockout_Gates (K1 through K9), THE Excel_Generator_Lambda SHALL retrieve gate status from AI_Evaluation_Result knockout_status map
2. WHEN a Knockout_Gate has status in knockout_status map, THE Excel_Generator_Lambda SHALL locate the gate row in Excel template (rows 60-90)
3. WHEN a Knockout_Gate status is retrieved, THE Excel_Generator_Lambda SHALL populate the gate status column (typically column G) with "PASS" or "FAIL"
4. WHEN a Knockout_Gate is linked to specific criteria, THE Excel_Generator_Lambda SHALL determine gate status based on linked criteria coverage (FAIL if any linked critical criterion has coverage "None" or weak "Partial")
5. WHEN a Knockout_Gate has no linked criteria (K8, K9), THE Excel_Generator_Lambda SHALL default status to "PASS" unless explicitly flagged in AI_Evaluation_Result

### Requirement 5: Total Score and Grade Band Population

**User Story:** As a reviewer, I want the final total score, grade band, and verdict clearly displayed in the Excel scorecard, so that I can understand the overall evaluation outcome.

#### Acceptance Criteria

1. WHEN total_score is available in AI_Evaluation_Result scoring section, THE Excel_Generator_Lambda SHALL populate the total score cell (typically row 60-70 range) with the numeric total_score value
2. WHEN grade_band is available in AI_Evaluation_Result scoring section, THE Excel_Generator_Lambda SHALL populate the grade band cell with the band label (e.g., "B — Pass", "A — Excellent")
3. WHEN verdict is available in AI_Evaluation_Result scoring section, THE Excel_Generator_Lambda SHALL populate the verdict cell with the verdict text
4. WHEN all_knockouts_pass is FALSE in AI_Evaluation_Result, THE Excel_Generator_Lambda SHALL override grade_band to "INVALID" and verdict to "AUDIO PD INVALID — One or more knock-out gates failed"
5. WHEN total_score calculation results in a value, THE Excel_Generator_Lambda SHALL ensure the score is rounded to one decimal place before population

### Requirement 6: Red Flags Population in Excel Scorecard

**User Story:** As a reviewer, I want any red flags raised during evaluation to be listed in the Excel scorecard, so that I can identify critical issues requiring immediate attention.

#### Acceptance Criteria

1. WHEN AI_Evaluation_Result contains red_flags array with entries, THE Excel_Generator_Lambda SHALL locate the red flags section in Excel template
2. FOR ALL Red_Flags in the red_flags array, THE Excel_Generator_Lambda SHALL populate a red flag row with the red flag code (RF-1 through RF-10)
3. FOR ALL Red_Flags in the red_flags array, THE Excel_Generator_Lambda SHALL populate the red flag row with the trigger description
4. FOR ALL Red_Flags in the red_flags array, THE Excel_Generator_Lambda SHALL populate the red flag row with the required action
5. WHEN no red flags are present in AI_Evaluation_Result, THE Excel_Generator_Lambda SHALL leave the red flags section empty or mark as "None"

### Requirement 7: Call Summary Sheet Population

**User Story:** As a reviewer, I want a Call Summary sheet in the Excel scorecard with key evaluation highlights, so that I can quickly review the assessment without reading all details.

#### Acceptance Criteria

1. WHEN Excel_Generator_Lambda generates a scorecard, THE Excel_Generator_Lambda SHALL access or create a "Call Summary" sheet in the Excel workbook
2. WHEN populating the Call Summary sheet, THE Excel_Generator_Lambda SHALL include application_id, customer_name, total_score, grade_band, and verdict
3. WHEN populating the Call Summary sheet, THE Excel_Generator_Lambda SHALL list all Knockout_Gates with their PASS/FAIL status
4. WHEN populating the Call Summary sheet, THE Excel_Generator_Lambda SHALL list all Red_Flags if any were raised
5. WHEN populating the Call Summary sheet, THE Excel_Generator_Lambda SHALL include a breakdown of section scores showing sections with coverage below 60%

### Requirement 8: Gap and Coaching Report Auto-Generation

**User Story:** As an operations manager, I want a Gap & Coaching Report automatically generated for evaluations with score below 75, so that I can provide targeted feedback to RCMs.

#### Acceptance Criteria

1. WHEN total_score in AI_Evaluation_Result is less than 75, THE Excel_Generator_Lambda SHALL generate a Gap & Coaching Report section
2. WHEN generating the Gap & Coaching Report, THE Excel_Generator_Lambda SHALL list all criteria where coverage is "None" or "Partial"
3. WHEN generating the Gap & Coaching Report, THE Excel_Generator_Lambda SHALL include the criterion ID, criterion description, current coverage, and recommended coaching action for each gap
4. WHEN generating the Gap & Coaching Report, THE Excel_Generator_Lambda SHALL identify the 3 sections with lowest coverage_percentage as priority coaching areas
5. WHEN total_score is 75 or above, THE Excel_Generator_Lambda SHALL skip Gap & Coaching Report generation

### Requirement 9: Frontend Real-Time Status Tracking During Processing

**User Story:** As a user, I want to see real-time status updates as my audio file moves through the processing pipeline, so that I understand what stage the evaluation is in and how long to wait.

#### Acceptance Criteria

1. WHEN a user uploads an audio file, THE Frontend SHALL display initial status "UPLOADED" with a progress indicator
2. WHILE evaluation is in progress, THE Polling_Service SHALL query DynamoDB_Evaluation_Table every 5 seconds for status updates
3. WHEN DynamoDB_Evaluation_Table status changes, THE Frontend SHALL update the displayed status to the new value (PREPROCESSING → TRANSCRIBING → EVALUATING → EXCEL_GENERATING → COMPLETED)
4. WHEN status is "TRANSCRIBING", THE Frontend SHALL display estimated time remaining as "3-5 minutes remaining"
5. WHEN status is "EVALUATING", THE Frontend SHALL display estimated time remaining as "1-2 minutes remaining"
6. WHEN status is "EXCEL_GENERATING", THE Frontend SHALL display estimated time remaining as "10 seconds remaining"
7. WHEN status reaches "COMPLETED", THE Polling_Service SHALL stop polling and display completion message with action buttons
8. WHEN status reaches "FAILED", THE Frontend SHALL display error message with error_message from DynamoDB_Evaluation_Table

### Requirement 10: Post-Completion Action Buttons in Frontend

**User Story:** As a user, I want to see action buttons (View Transcription, View Excel, Download Excel) after processing completes, so that I can access evaluation results immediately.

#### Acceptance Criteria

1. WHEN status reaches "COMPLETED", THE Frontend SHALL display a "View Transcription" button
2. WHEN user clicks "View Transcription" button, THE Frontend SHALL fetch transcript from S3 using transcript_s3_key from DynamoDB_Evaluation_Table and display in Transcript_Viewer component
3. WHEN status reaches "COMPLETED" and excel_s3_key is present in DynamoDB_Evaluation_Table, THE Frontend SHALL display a "Download Excel" button
4. WHEN user clicks "Download Excel" button, THE Frontend SHALL call API endpoint to generate pre-signed S3 URL and trigger browser download
5. WHEN status reaches "COMPLETED", THE Frontend SHALL display a "View Excel Preview" button (optional enhancement)
6. WHEN user clicks "View Excel Preview" button, THE Frontend SHALL display Excel_Preview component showing scorecard data in HTML format
7. WHEN action buttons are displayed, THE Frontend SHALL show completion timestamp and total processing duration

### Requirement 11: Transcript Viewer Component in Frontend

**User Story:** As a user, I want to view the full call transcript with speaker labels and timestamps in the frontend, so that I can review the conversation without downloading files.

#### Acceptance Criteria

1. WHEN Transcript_Viewer component is opened, THE Frontend SHALL fetch transcript JSON from S3 using transcript_s3_key
2. WHEN transcript JSON is loaded, THE Transcript_Viewer SHALL display the full transcript text at the top
3. WHEN transcript JSON contains segments array, THE Transcript_Viewer SHALL display each segment with timestamp, speaker label, and text
4. WHEN displaying segments, THE Transcript_Viewer SHALL format timestamps as MM:SS
5. WHEN displaying segments, THE Transcript_Viewer SHALL apply different visual styling for RCM vs Customer speaker labels
6. WHEN Transcript_Viewer is open, THE Frontend SHALL provide a search/filter capability to find specific keywords in the transcript
7. WHEN user searches for a keyword, THE Transcript_Viewer SHALL highlight matching text and scroll to first match

### Requirement 12: History Tab with All Previous Evaluations

**User Story:** As a user, I want to see a history of all my previous audio evaluations with key details, so that I can track evaluation trends and access past results.

#### Acceptance Criteria

1. WHEN user navigates to History_Tab, THE Frontend SHALL query DynamoDB_Evaluation_Table for all evaluations ordered by created_at descending
2. WHEN evaluations are retrieved, THE History_Tab SHALL display a table with columns: Application ID, Customer Name, Upload Date, Score, Band, Status
3. WHEN an evaluation in history has status "COMPLETED", THE History_Tab SHALL display the total_score and grade_band
4. WHEN an evaluation in history has status "PROCESSING" or "FAILED", THE History_Tab SHALL display the current status instead of score
5. WHEN evaluation count exceeds 50, THE History_Tab SHALL implement pagination with 50 evaluations per page
6. WHEN History_Tab is displayed, THE Frontend SHALL provide filter dropdowns for status (ALL / COMPLETED / FAILED / PROCESSING) and date range

### Requirement 13: View and Download Actions from History Tab

**User Story:** As a user, I want to view transcripts and download Excel scorecards directly from the history list, so that I can access previous evaluation results without re-uploading files.

#### Acceptance Criteria

1. FOR ALL evaluations in History_Tab with status "COMPLETED", THE Frontend SHALL display a "View Details" button in the action column
2. WHEN user clicks "View Details" button, THE Frontend SHALL open a modal dialog showing evaluation summary with score, band, section breakdown, and knockout gate status
3. WHEN evaluation details modal is open, THE Frontend SHALL display "View Transcript" and "Download Excel" buttons
4. WHEN user clicks "View Transcript" from history, THE Frontend SHALL fetch and display the transcript in Transcript_Viewer component
5. WHEN user clicks "Download Excel" from history, THE Frontend SHALL generate pre-signed S3 URL and trigger download using excel_s3_key from DynamoDB_Evaluation_Table
6. WHEN evaluation does not have excel_s3_key populated, THE Frontend SHALL disable "Download Excel" button and show tooltip "Excel not yet generated"

### Requirement 14: Status Polling Service with Exponential Backoff

**User Story:** As a system, I want the frontend polling service to use efficient polling strategies, so that I minimize unnecessary API calls while providing timely status updates.

#### Acceptance Criteria

1. WHEN Polling_Service starts polling for an evaluation, THE Polling_Service SHALL poll every 5 seconds for the first 2 minutes
2. WHEN 2 minutes have elapsed since polling started, THE Polling_Service SHALL increase polling interval to every 10 seconds
3. WHEN 5 minutes have elapsed since polling started, THE Polling_Service SHALL increase polling interval to every 30 seconds
4. WHEN status reaches "COMPLETED" or "FAILED", THE Polling_Service SHALL stop polling immediately
5. WHEN polling encounters 3 consecutive errors, THE Polling_Service SHALL stop polling and display error message to user
6. WHEN user navigates away from upload page, THE Polling_Service SHALL stop polling for that evaluation
7. WHEN Polling_Service makes an API call, THE Polling_Service SHALL include evaluation_id and created_at as query parameters

### Requirement 15: Excel Template Cell Mapping Configuration

**User Story:** As a system, I want the Excel_Generator_Lambda to use a configurable cell mapping, so that template structure changes do not require code modifications.

#### Acceptance Criteria

1. WHEN Excel_Generator_Lambda initializes, THE Excel_Generator_Lambda SHALL load cell mapping configuration from environment variable or config file
2. WHEN cell mapping configuration is loaded, THE Excel_Generator_Lambda SHALL parse mapping for header cells (application_id → B3, customer_name → B4, etc.)
3. WHEN cell mapping configuration is loaded, THE Excel_Generator_Lambda SHALL parse mapping for criterion rows (A1 → row 12, A2 → row 13, ..., J3 → row 59)
4. WHEN cell mapping configuration is loaded, THE Excel_Generator_Lambda SHALL parse mapping for section subtotal rows
5. WHEN cell mapping configuration is loaded, THE Excel_Generator_Lambda SHALL parse mapping for knockout gate rows
6. WHEN populating Excel cells, THE Excel_Generator_Lambda SHALL use the loaded mapping instead of hardcoded cell references
7. WHEN a mapped cell reference is invalid, THE Excel_Generator_Lambda SHALL log a warning and skip that field population without failing the entire generation

### Requirement 16: DynamoDB Schema Extension for Enhanced Tracking

**User Story:** As a system, I want extended DynamoDB fields to track all processing stages and result locations, so that the frontend can provide complete status information.

#### Acceptance Criteria

1. WHEN an evaluation record is created in DynamoDB_Evaluation_Table, THE ingestion Lambda SHALL initialize status field as "UPLOADED"
2. WHEN preprocessing starts, THE preprocessing Lambda SHALL update status to "PREPROCESSING" and set preprocessing_started_at timestamp
3. WHEN transcription starts, THE transcription worker SHALL update status to "TRANSCRIBING" and set transcription_started_at timestamp
4. WHEN evaluation starts, THE evaluation Lambda SHALL update status to "EVALUATING" and set evaluation_started_at timestamp
5. WHEN Excel generation starts, THE Excel_Generator_Lambda SHALL update status to "EXCEL_GENERATING" and set excel_generation_started_at timestamp
6. WHEN Excel generation completes, THE Excel_Generator_Lambda SHALL update status to "COMPLETED", set completed_at timestamp, and populate excel_s3_key field
7. WHEN any stage fails, THE responsible Lambda SHALL update status to "FAILED", set failed_at timestamp, and populate error_message field with failure details

### Requirement 17: API Endpoint for Transcript Retrieval

**User Story:** As a frontend, I want a dedicated API endpoint to retrieve transcript content, so that I can display transcripts without exposing S3 keys to the client.

#### Acceptance Criteria

1. THE API Gateway SHALL expose endpoint GET /evaluations/{evaluation_id}/transcript
2. WHEN GET /evaluations/{evaluation_id}/transcript is called, THE API Lambda SHALL retrieve evaluation record from DynamoDB_Evaluation_Table using evaluation_id
3. WHEN evaluation record is retrieved, THE API Lambda SHALL fetch transcript JSON from S3 using transcript_s3_key
4. WHEN transcript JSON is fetched, THE API Lambda SHALL return transcript content with status 200 and content-type application/json
5. WHEN transcript_s3_key is null or transcript file does not exist in S3, THE API Lambda SHALL return status 404 with error message "Transcript not found"
6. WHEN evaluation_id does not exist in DynamoDB, THE API Lambda SHALL return status 404 with error message "Evaluation not found"
7. WHEN API Lambda encounters S3 access error, THE API Lambda SHALL return status 500 with error message "Failed to retrieve transcript"

### Requirement 18: Error Handling and Retry for Excel Generation

**User Story:** As a system, I want robust error handling and retry logic in Excel generation, so that temporary failures do not result in missing scorecards.

#### Acceptance Criteria

1. WHEN Excel_Generator_Lambda encounters S3 template download failure, THE Excel_Generator_Lambda SHALL retry download up to 3 times with exponential backoff
2. WHEN Excel_Generator_Lambda encounters openpyxl parsing error, THE Excel_Generator_Lambda SHALL log detailed error message and return status 500 without retrying
3. WHEN Excel_Generator_Lambda encounters missing fields in AI_Evaluation_Result, THE Excel_Generator_Lambda SHALL populate available fields and log warnings for missing data
4. WHEN Excel_Generator_Lambda encounters S3 upload failure for generated scorecard, THE Excel_Generator_Lambda SHALL retry upload up to 3 times
5. WHEN all retries are exhausted, THE Excel_Generator_Lambda SHALL update DynamoDB status to "FAILED" with error_message indicating Excel generation failure
6. WHEN Excel_Generator_Lambda successfully generates scorecard, THE Excel_Generator_Lambda SHALL update DynamoDB with excel_s3_key and status "COMPLETED"
7. WHEN evaluation Lambda fails to trigger Excel_Generator_Lambda, THE evaluation Lambda SHALL log error but complete successfully with status "COMPLETED" (Excel generation failure should not fail evaluation)

### Requirement 19: Frontend Loading States and Error Messages

**User Story:** As a user, I want clear loading indicators and error messages during processing, so that I understand system status and know when intervention is needed.

#### Acceptance Criteria

1. WHEN Frontend is fetching data from API, THE Frontend SHALL display a loading spinner or skeleton screen
2. WHEN API call fails with network error, THE Frontend SHALL display error message "Network error. Please check your connection and try again."
3. WHEN API call fails with 404 status, THE Frontend SHALL display error message specific to the resource (e.g., "Evaluation not found" or "Transcript not available yet")
4. WHEN API call fails with 500 status, THE Frontend SHALL display error message "Server error. Please try again later or contact support."
5. WHEN status is "FAILED" in History_Tab, THE Frontend SHALL display error icon and allow user to view error details by clicking
6. WHEN user views error details, THE Frontend SHALL display error_message from DynamoDB in a modal dialog
7. WHEN Frontend displays error messages, THE Frontend SHALL provide a "Retry" button for operations that can be retried

### Requirement 20: Performance Optimization for History List

**User Story:** As a user with many evaluations, I want the history list to load quickly, so that I can access past results without delays.

#### Acceptance Criteria

1. WHEN History_Tab queries DynamoDB, THE Frontend SHALL request only the first 50 evaluations sorted by created_at descending
2. WHEN user scrolls to bottom of history list, THE Frontend SHALL load next 50 evaluations using pagination (lazy loading)
3. WHEN History_Tab is displayed, THE Frontend SHALL cache evaluation list in memory for 5 minutes to avoid redundant queries
4. WHEN user applies filters, THE Frontend SHALL query DynamoDB with filter parameters using Global Secondary Index on status field
5. WHEN DynamoDB query takes longer than 3 seconds, THE Frontend SHALL display a loading message "Loading evaluations..."
6. WHEN History_Tab component unmounts, THE Frontend SHALL cancel any pending API requests
7. WHEN evaluation list exceeds 500 entries, THE Frontend SHALL display a message recommending date range filter to improve performance
