import unittest
from unittest.mock import patch, mock_open
import json

from src.reconcile_sh import normalize_company_name, run_reconcile

class TestNormalize(unittest.TestCase):
    def test_normalization(self):
        pairs = [
            ("Compusult", "Compusult Limited"),
            ("Solace Power", "Solace Power Inc"),
            ("Trudell Medical", "Trudell Medical Limited"),
            ("Vish", "Vish Limited"),
            ("Virtual Marine", "Virtual Marine Inc."),
            ("CoLab Software", "CoLab"),
            ("Other Company Inc.", "Other Company"),
            ("WSP", "WSP"),
            ("Test-Comp", "Test Comp")
        ]
        for a, b in pairs:
            self.assertEqual(normalize_company_name(a), normalize_company_name(b), f"Failed matching {a} and {b}")

class TestBucketing(unittest.TestCase):
    @patch('src.reconcile_sh.httpx.Client')
    @patch('src.reconcile_sh.os.path.exists')
    @patch('builtins.open')
    def test_bucketing(self, mock_file, mock_exists, mock_client):
        # Mock SH API
        class MockResponse:
            def json(self):
                return {
                    "data": [
                        {"companyName": "In Roster But No Jobs", "url": "url1"},
                        {"companyName": "In Roster And Jobs Inc.", "url": "url2"},
                        {"companyName": "Not In Roster Ltd", "url": "url3"},
                        {"companyName": "Not In Roster Ltd", "url": "url4"},
                        {"companyName": "Trudell Medical", "url": "url5"}
                    ],
                    "pagination": {"hasMore": False}
                }
            def raise_for_status(self): pass

        mock_instance = mock_client.return_value.__enter__.return_value
        mock_instance.get.return_value = MockResponse()
        
        # Mock OS path exists
        mock_exists.return_value = True
        
        # Mock open files content
        roster_data = [
            {"company_name": "In Roster But No Jobs"},
            {"company_name": "In Roster And Jobs"},
            {"company_name": "Trudell Medical Limited"}
        ]
        open_jobs_data = [
            {"company": "In Roster And Jobs"},
            {"company": "Trudell Medical"}
        ]
        
        mock_file.side_effect = [
            mock_open(read_data=json.dumps(roster_data)).return_value,
            mock_open(read_data=json.dumps(open_jobs_data)).return_value,
            mock_open().return_value
        ]
        
        # We need to capture the json dump to check buckets
        with patch('src.reconcile_sh.json.dump') as mock_dump:
            run_reconcile()
            
            args, kwargs = mock_dump.call_args
            out_data = args[0]
            
            not_in_roster = out_data["not_in_roster"]
            scrape_gap = out_data["scrape_gap"]
            overlap = out_data["overlap"]
            
            self.assertEqual(len(not_in_roster), 1)
            self.assertEqual(not_in_roster[0]["company_name"], "Not In Roster Ltd")
            self.assertEqual(not_in_roster[0]["job_count"], 2)
            
            self.assertEqual(len(scrape_gap), 1)
            self.assertEqual(scrape_gap[0]["company_name"], "In Roster But No Jobs")
            
            self.assertEqual(overlap, 2)  # In Roster And Jobs, Trudell Medical

if __name__ == '__main__':
    unittest.main()
