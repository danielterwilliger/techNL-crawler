## Goal
Create `0_seed_companies.ps1` to automatically pull the latest member directory from techNL and update our local source of truth (`techNL_companies.md`) with any new companies.

## Context
Before we can map career pages, we need an accurate list of companies. The techNL member directory dynamically renders all members on a single page at `https://members.technl.ca/memberdirectory/FindStartsWith?term=%23%21`. This script will scrape that page, identify new companies not already in our markdown file, fetch their actual website URLs, and append them to the markdown list.

## Tasks
1. **Create `0_seed_companies.ps1`**:
   - Fetch the raw HTML of the member directory: `Invoke-RestMethod -Uri "https://members.technl.ca/memberdirectory/FindStartsWith?term=%23%21"`
   - Use regular expressions or HTML parsing to extract all company names and their relative detail links (e.g., `href="//members.technl.ca/memberdirectory/Details/abbatek-group-inc-4462194"`).
   - Read the existing `techNL_companies.md` file. Parse the existing Markdown table to get a list of currently known company names.
   - Compare the scraped companies against the known companies to identify *new* companies.
   - For each *new* company:
     - The scraped detail link is an internal techNL page. Fetch this detail page (e.g., `https://members.technl.ca/memberdirectory/Details/...`).
     - Parse the detail page HTML to find the company's actual external website URL (often under a "Visit Website" link or a specific class/element). *Note: Since this is raw HTML parsing, AI is not strictly required, but you can use `gemini` if the HTML structure is too complex for regex.*
     - If an external website is found, format it as a markdown table row: `| **Company Name** | Location (if found, else N/A) | [website.com](https://website.com) |`
     - Append the new row to the end of the table in `techNL_companies.md`.
   - If no new companies are found, log a message and exit cleanly.

2. **Update Orchestrator**:
   - Update `techNL_crawler/run_pipeline.ps1` to include `0_seed_companies.ps1` as the very first script in the `$Scripts` array.