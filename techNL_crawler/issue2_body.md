## Goal
Create `2_find_new_jobs.ps1` to act as a "Scout" that finds new job postings on known career pages without duplicating effort.

## Context
Now that we have a list of verified career pages in `companies_state.json` (from Stage 1), we need to periodically check these pages for new job postings that might be a fit, logging them into a stateful file `known_jobs.json`.

## Tasks
1. **Define Schema**: Create/define `known_jobs.json`. It should track discovered jobs with fields:
   - `company_name` (string)
   - `job_url` (string)
   - `discovery_date` (ISO 8601 string)
   - `status` (enum: "pending_review", "issue_created", "rejected")

2. **Create `2_find_new_jobs.ps1`**:
   - Read `companies_state.json` and filter for companies with a `career_page_url` and `status == "active"`.
   - Read `resumes_and_profile/USER_PROFILE.md` (one level up) to get context on the applicant's skills and desired roles.
   - Read `known_jobs.json` (create if it doesn't exist).
   - Loop through the active career URLs. For each, use the `gemini` CLI (`gemini -m gemini-3-flash-preview ... --yolo`).
   - The prompt should provide the `USER_PROFILE.md` context and the career page URL. Ask Gemini to visit the URL and return a JSON array of URLs for *any* open job postings that loosely match the profile.
   - Parse the JSON array response.
   - For each returned job URL, check if it already exists in `known_jobs.json`.
   - If it is new, append it to `known_jobs.json` with the current date and `status = "pending_review"`.
   - Save the `known_jobs.json` file.