import unittest

from app.agent import _history_messages


class AgentHistoryTests(unittest.TestCase):
    def test_excludes_current_message_already_in_history(self):
        history = [
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Current question"},
        ]

        self.assertEqual(
            _history_messages(history, "Current question"),
            [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
            ],
        )

    def test_preserves_history_when_current_message_is_not_included(self):
        history = [{"role": "user", "content": "Earlier question"}]

        self.assertEqual(
            _history_messages(history, "Current question"),
            history,
        )

    def test_limits_history_to_latest_sixteen_messages(self):
        history = [
            {"role": "user", "content": f"Question {index}"}
            for index in range(20)
        ]

        self.assertEqual(
            _history_messages(history, "Current question"),
            history[-16:],
        )


if __name__ == "__main__":
    unittest.main()
