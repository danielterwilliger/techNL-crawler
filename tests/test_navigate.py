import unittest
from src.navigate import pick_title

class TestNavigatePickTitle(unittest.TestCase):
    def test_dns_case(self):
        # Digital Nova Scotia case: banner h1 + banner page title + real slug
        anchor = "JOB PORTAL"
        h1 = "JOB PORTAL"
        page_title = "JOB PORTAL - Digital Nova Scotia"
        json_ld_title = None
        url = "https://digitalnovascotia.com/job-posts/senior-business-analyst-healthcare-transformation-2"
        
        expected = "Senior Business Analyst Healthcare Transformation"
        result = pick_title(anchor, h1, page_title, json_ld_title, url)
        self.assertEqual(result, expected)

    def test_dns_case_with_trailing_slash(self):
        anchor = "JOB PORTAL"
        h1 = "JOB PORTAL"
        page_title = "JOB PORTAL - Digital Nova Scotia"
        json_ld_title = None
        url = "https://digitalnovascotia.com/job-posts/senior-business-analyst-healthcare-transformation-2/"
        
        expected = "Senior Business Analyst Healthcare Transformation"
        result = pick_title(anchor, h1, page_title, json_ld_title, url)
        self.assertEqual(result, expected)

    def test_normal_board_h1_wins(self):
        # Real h1 wins over everything else if there is no JSON-LD
        anchor = "Apply Now"
        h1 = "Software Engineer - Backend"
        page_title = "Careers at TechCorp"
        json_ld_title = None
        url = "https://techcorp.com/jobs/software-engineer-backend"
        
        expected = "Software Engineer - Backend"
        result = pick_title(anchor, h1, page_title, json_ld_title, url)
        self.assertEqual(result, expected)

    def test_json_ld_wins(self):
        # JSON-LD wins over h1 and everything else
        anchor = "Apply Now"
        h1 = "Software Engineer - Backend"
        page_title = "Careers at TechCorp"
        json_ld_title = "Senior Software Engineer (Backend Systems)"
        url = "https://techcorp.com/jobs/software-engineer-backend"
        
        expected = "Senior Software Engineer (Backend Systems)"
        result = pick_title(anchor, h1, page_title, json_ld_title, url)
        self.assertEqual(result, expected)

    def test_slugless_url_unchanged(self):
        # Slugless URL uses h1 or fallback
        anchor = "Apply"
        h1 = "JOB PORTAL"
        page_title = "Awesome Job - TechCorp"
        json_ld_title = None
        url = "https://techcorp.com/jobs/?id=12345"
        
        # h1 is "JOB PORTAL" which is junk.
        # slug_part is "", which gives no valid slug title.
        # So it falls back to best_title.
        # best_title("Apply", "Awesome Job - TechCorp") gives "Awesome Job" because "Apply" is generic
        # Wait, GENERIC_TITLES has "apply", so it splits page_title on space-dash-space
        
        expected = "Awesome Job"
        result = pick_title(anchor, h1, page_title, json_ld_title, url)
        self.assertEqual(result, expected)

    def test_valid_slug_no_h1(self):
        # Valid slug but no h1 and no JSON-LD
        anchor = "Click Here"
        h1 = ""
        page_title = "Careers"
        json_ld_title = None
        url = "https://company.com/openings/data-scientist-machine-learning"
        
        expected = "Data Scientist Machine Learning"
        result = pick_title(anchor, h1, page_title, json_ld_title, url)
        self.assertEqual(result, expected)

    def test_slug_with_multiple_dashes_and_numbers(self):
        anchor = "Job"
        h1 = "JOB PORTAL"
        page_title = "Job Portal"
        json_ld_title = None
        url = "https://digitalnovascotia.com/job-posts/it-support-specialist-1-2"
        
        expected = "It Support Specialist 1"
        result = pick_title(anchor, h1, page_title, json_ld_title, url)
        self.assertEqual(result, expected)

if __name__ == '__main__':
    unittest.main()
