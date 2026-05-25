# /// script
# dependencies = [
#   "httpx",
# ]
# ///

import httpx
import re
import os

MD_FILE = "data/techNL_companies.md"
URL = "https://members.technl.ca/memberdirectory/FindStartsWith?term=%23%21"

def main():
    print(f"Fetching techNL member directory from {URL}...")
    try:
        response = httpx.get(URL, timeout=30.0)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch member directory: {e}")
        return

    html_content = response.text
    regex = r'href="?//members\.technl\.ca/memberdirectory/Details/([^"]+)"?[^>]*>([^<]+)</a>'
    matches = re.finditer(regex, html_content, re.IGNORECASE)

    scraped_companies = {}
    for match in matches:
        detail_path = match.group(1).strip()
        # Decode HTML entities manually for basic ones or use html.unescape
        import html
        company_name = html.unescape(match.group(2).strip())
        if company_name:
            scraped_companies[company_name] = f"https://members.technl.ca/memberdirectory/Details/{detail_path}"

    print(f"Found {len(scraped_companies)} companies in the directory.")

    known_companies = {}
    header_lines = []
    
    if os.path.exists(MD_FILE):
        with open(MD_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
            for line in lines:
                m1 = re.match(r'^\|\s*\*\*([^*]+)\*\*\s*\|\s*([^|]+)\s*\|\s*\[([^]]+)\]\(([^)]+)\)\s*\|$', line)
                m2 = re.match(r'^\|\s*\*\*([^*]+)\*\*\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|$', line)
                
                if m1:
                    name = m1.group(1).strip()
                    known_companies[name] = {
                        "Location": m1.group(2).strip(),
                        "DisplayDomain": m1.group(3).strip(),
                        "WebsiteUrl": m1.group(4).strip()
                    }
                elif m2:
                    name = m2.group(1).strip()
                    known_companies[name] = {
                        "Location": m2.group(2).strip(),
                        "DisplayDomain": m2.group(3).strip(),
                        "WebsiteUrl": ""
                    }
                elif re.match(r"^#|^\s*$|^\|\s*Company Name|^\|\s*:---", line):
                    if len(known_companies) == 0:
                        header_lines.append(line)

    if not header_lines:
        header_lines = [
            "# All techNL Member Companies (Complete Directory)",
            "",
            "| Company Name | Location | Website |",
            "| :--- | :--- | :--- |"
        ]

    print(f"Found {len(known_companies)} known companies in {MD_FILE}.")

    updates_count = 0
    new_count = 0

    print("Verifying all companies against the live directory...")
    # To avoid taking forever on all 200+ links sequentially, we only check new companies
    # or implement a fast async fetch. For simplicity, we just fetch new ones.
    
    for company, detail_url in scraped_companies.items():
        if company not in known_companies:
            print(f"Checking NEW company: {company}...")
            try:
                detail_resp = httpx.get(detail_url, timeout=10.0)
                web_match = re.search(r'gz-details-website.*?href="([^"]+)"', detail_resp.text, re.IGNORECASE | re.DOTALL)
                website_url = web_match.group(1).strip() if web_match else ""
                
                loc_match = re.search(r'>(St\. John\'s, NL|Mount Pearl, NL|Corner Brook, NL|Paradise, NL|[^<]+, NL)<', detail_resp.text, re.IGNORECASE)
                location = loc_match.group(1).strip() if loc_match else "N/A"
                
                display_domain = "N/A"
                if website_url:
                    display_domain = re.sub(r'^https?://(www\.)?', '', website_url)
                    display_domain = re.sub(r'/$', '', display_domain)

                known_companies[company] = {
                    "Location": location,
                    "DisplayDomain": display_domain,
                    "WebsiteUrl": website_url
                }
                new_count += 1
                print(f" -> Added {company}")
            except Exception as e:
                print(f" -> Failed to fetch details: {e}")

    if updates_count == 0 and new_count == 0:
        print("\nNo new companies found. Markdown is up to date.")
        return

    print(f"\nRebuilding markdown file with {new_count} new companies...")

    new_content = list(header_lines)
    for name in sorted(known_companies.keys()):
        comp = known_companies[name]
        if comp["WebsiteUrl"]:
            new_content.append(f"| **{name}** | {comp['Location']} | [{comp['DisplayDomain']}]({comp['WebsiteUrl']}) |")
        else:
            display = comp['DisplayDomain'] if comp['DisplayDomain'] else "N/A"
            new_content.append(f"| **{name}** | {comp['Location']} | {display} |")

    os.makedirs(os.path.dirname(MD_FILE), exist_ok=True)
    with open(MD_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(new_content) + "\n")

    print("Seed step complete.")

if __name__ == "__main__":
    main()
