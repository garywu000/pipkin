import unittest

from server import clean_title


class TitleTest(unittest.TestCase):
    def test_model_titles_are_cleaned_or_rejected(self):
        self.assertEqual(clean_title('"study session page from brief."'), "Study session page from brief")
        self.assertEqual(clean_title("  Heat pump\nefficiency  "), "Heat pump efficiency")
        self.assertEqual(clean_title("**Word agenda for Thursday**"), "Word agenda for Thursday")
        for bad in ["", "x", None, 42, "{\"title\": 1}", "one two three four five six seven eight nine", "a" * 80]:
            with self.subTest(bad=bad):
                self.assertIsNone(clean_title(bad))


if __name__ == "__main__":
    unittest.main()
