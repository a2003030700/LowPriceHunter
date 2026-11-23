import unittest

from ps5hunter.scraper import _post_text_from_title


class PostTitleTest(unittest.TestCase):
    def test_group_and_post(self):
        title = "(2) 某社團 | 【售】PS5 主機 5000 | Facebook"
        self.assertEqual(_post_text_from_title(title), "【售】PS5 主機 5000")

    def test_post_only(self):
        self.assertEqual(_post_text_from_title("【售】PS5 5000 | Facebook"), "【售】PS5 5000")

    def test_pipe_in_post(self):
        title = "社團 | A | B | Facebook"
        self.assertEqual(_post_text_from_title(title), "A | B")


if __name__ == "__main__":
    unittest.main()
