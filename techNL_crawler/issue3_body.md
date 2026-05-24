## Goal
Create `3_verify_and_create_issues.ps1` for deep AI validation and integration with GitHub Issues (The CRM).

## Context
Stage 2 generates a list of potential leads in `known_jobs.json` marked as `"pending_review"`. We need a final verification pass to ensure quality, and then automatically create GitHub issues for the good matches.

## Tasks
1. **Create `3_verify_and_create_issues.ps1`**:
   - Read `known_jobs.json` and filter for jobs where `status == "pending_review"`.
   - Read `resumes_and_profile/USER_PROFILE.md`.
   - Read `issue_template.md` (at the root of the repo) to understand the expected formatting for a job lead issue.
   - Loop through the pending jobs. For each, use the `gemini` CLI for a deep analysis.
   - The prompt should include the `USER_PROFILE.md`, the specific `job_url`, and ask for a strict evaluation: "Is this job a strong match? Reply strictly with YES or NO on the first line, followed by a brief 2-sentence rationale."
   - If the response starts with "YES":
     - Use the GitHub CLI to create an issue: `gh issue create --title "Lead: [Company Name] - [Job Title/Role]" --body "[Rationale and URL]"`. Try to map the output to `issue_template.md` format.
     - Update the job's status in `known_jobs.json` to `"issue_created"`.
   - If the response starts with "NO":
     - Update the job's status in `known_jobs.json` to `"rejected"`.
   - Save the updated `known_jobs.json`.