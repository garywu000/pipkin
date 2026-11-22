import json
import unittest

from server import humanize_messages, same_text


class HumanizeRequestTest(unittest.TestCase):
    def test_essay_mode_carries_instructions_and_prompt_separately(self):
        messages = humanize_messages("I helped him settle in.", "direct", "Make it a direct college essay",
                                     "Prompt: What have you done?", "essay")
        self.assertEqual(len(messages), 2, "essays skip the short-message examples")
        self.assertIn("college application essays", messages[0]["content"])
        self.assertIn("direct, concise", messages[0]["content"])
        fields = json.loads(messages[1]["content"])
        self.assertEqual(fields["draft"], "I helped him settle in.")
        self.assertEqual(fields["instructions"], "Make it a direct college essay")
        self.assertEqual(fields["prompt_or_context"], "Prompt: What have you done?")

    def test_message_mode_keeps_examples_and_omits_empty_fields(self):
        messages = humanize_messages("Can you send it?", "natural")
        self.assertGreater(len(messages), 2)
        self.assertEqual(json.loads(messages[-1]["content"]), {"tone": "natural", "draft": "Can you send it?"})

    def test_unchanged_detection_ignores_whitespace(self):
        self.assertTrue(same_text("A  b\nc ", "A b c"))
        self.assertFalse(same_text("A b c", "A b d"))


if __name__ == "__main__":
    unittest.main()


class WordCountTest(unittest.TestCase):
    def test_counts_match_google_docs_rules(self):
        from server import count_words
        cases = {"military-style school": 2, "word—word": 2, "I paid — attention": 3, "didn’t feel 2026 ok": 4,
                 "a, b & c": 3, "e-mail U.S. 3.5": 3, "": 0, "Line one\n\nLine two.": 4}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(count_words(text), expected)

    def test_limits_come_from_instructions_or_the_uc_piq(self):
        from server import word_limit
        self.assertEqual(word_limit("Keep it under 350 words"), 350)
        self.assertEqual(word_limit("a 650-word limit"), 650)
        self.assertEqual(word_limit("no more than 250 words please"), 250)
        self.assertEqual(word_limit("For my University of California Personal Insight Question"), 350)
        self.assertIsNone(word_limit("Make it sound warmer"))
