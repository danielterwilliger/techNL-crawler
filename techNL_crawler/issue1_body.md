## Goal
Move away from Markdown logging to a stateful JSON pipeline. Define the schema for `companies_state.json` and rewrite the initial scraping logic into a new script called `1_map_career_pages.ps1`.

## Context
The previous script (`run_job_search.ps1`) appended raw CLI output and results into a markdown file (`company_careers_pages.md`). This is not repeatable. We need a stateful system so the crawler knows what has been checked, what failed, and when it was last checked.

## Tasks
1. **Define Schema**: Create a JSON file/structure `companies_state.json`. It should track an array or dictionary of companies with fields:
   - `company_name` (string)
   - `website_url` (string)
   - `career_page_url` (string or null)
   - `last_checked_date` (ISO 8601 string)
   - `status` (enum: "active", "failed", "pending")

2. **Create `1_map_career_pages.ps1`**:
   - Parse the existing source file `techNL_companies.md` (in `techNL_crawler/`) to extract company names and website URLs.
   - Read `companies_state.json` (create if it doesn't exist).
   - Identify targets: Find companies from the MD file that are either missing from the JSON, haven't been checked in > 30 days, or have a 'failed' status.
   - For each target, use the `gemini` CLI to act as a web researcher. Use the command: `gemini -m gemini-3-flash-preview -p "<PROMPT>" --yolo`. The prompt should instruct it to find the careers page URL (ATS link, /careers, LinkedIn jobs) for the given website.
   - Capture the output. Handle errors gracefully (e.g., if Gemini returns "SCRAPE_FAILED" or network errors).
   - Update the state in `companies_state.json` (set `career_page_url`, update `last_checked_date`, set `status`).
   - Save the updated JSON. DO NOT write to markdown logs.