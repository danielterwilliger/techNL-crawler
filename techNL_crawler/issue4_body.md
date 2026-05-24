## Goal
Tie the pipeline together with an orchestration script and clean up deprecated files.

## Context
We now have three independent, stateful scripts. We need a single entry point to run them sequentially (like a cron job) and we need to clean up the repository from the old markdown-based approach.

## Tasks
1. **Create Orchestrator**:
   - Create a master script `run_pipeline.ps1` (or `run_pipeline.bat`) in the `techNL_crawler` directory.
   - The script should sequentially execute:
     1. `.\1_map_career_pages.ps1`
     2. `.\2_find_new_jobs.ps1`
     3. `.\3_verify_and_create_issues.ps1`
   - Add basic error handling or logging so if one script throws a fatal unhandled error, the pipeline stops. (Note: individual scripts should handle expected errors internally).

2. **Cleanup**:
   - Add `companies_state.json` and `known_jobs.json` to `.gitignore` so we don't bloat the git history with state data.
   - Delete the old output file `techNL_crawler/company_careers_pages.md`.
   - Delete the old script `techNL_crawler/run_job_search.ps1`.
   - Ensure the repository is clean and only contains the new architecture.