import os
import sys
import json
import urllib.request
import urllib.parse
import asyncio
import importlib

# Add current directory to path
sys.path.append(os.path.dirname(__file__))

find_new_jobs_module = importlib.import_module("2_find_new_jobs")
clean_html_for_scouting = find_new_jobs_module.clean_html_for_scouting
render_page_in_browser = find_new_jobs_module.render_page_in_browser

def dump_pages():
    targets = {
        "CIBC": "https://www.cibc.com/en/about-cibc/careers.html",
        "CoLab Software": "https://www.colabsoftware.com/careers",
        "Vision33 Inc.": "https://www.vision33.com/careers"
    }
    
    out_dir = os.path.join(os.path.dirname(__file__), "dumped_text")
    os.makedirs(out_dir, exist_ok=True)
    
    for name, url in targets.items():
        print(f"Rendering {name} ({url})...")
        html = render_page_in_browser(url, wait_seconds=8)
        if html:
            cleaned = clean_html_for_scouting(html)
            raw_path = os.path.join(out_dir, f"{name.lower().replace(' ', '_')}_raw.html")
            clean_path = os.path.join(out_dir, f"{name.lower().replace(' ', '_')}_clean.txt")
            
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(html)
            with open(clean_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            print(f"Saved {name} to {clean_path} and {raw_path}")
        else:
            print(f"Failed to render {name}")

if __name__ == '__main__':
    dump_pages()
