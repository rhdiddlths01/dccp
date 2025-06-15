import atexit
import datetime
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from snowflake.snowpark import Session
from snowflake.cortex import complete
import math
from collections import Counter

load_dotenv()

class Solver:
    def __init__(self):
        self.session = self._init_snowflake()
        self.model = "claude-3-5-sonnet"
        self.problems = {}
        self.guess_history = {}
        self.snowflake_calls = 0
        self.log_file = open("run.log", "a")
        atexit.register(self.cleanup)

    def _init_snowflake(self):
        connection_params = {
            "account": "NPSRWTY-ZGB66966",
            "user": "AHNCW",
            "password": "Ch0772****####",
            "role": "ACCOUNTADMIN",
            "database": "<none selected>",
            "schema": "<none selected>",
            "warehouse": "<none selected>",
        }
        return Session.builder.configs(connection_params).create()

    def cleanup(self):
        try:
            self.log_file.close()
        except:
            pass
        try:
            self.session.close()
        except:
            pass

    def start_problem(self, problem_id, candidate_words):
        self.problems[problem_id] = {
            "candidate_words": candidate_words,
            "feedback_history": [],
        }
        self.guess_history[problem_id] = []
        self._log(f"\n=== Starting Problem {problem_id} ===")
        self._log(f"Candidate words: {', '.join(candidate_words)}")

    def add_feedback(self, problem_id, verbal_feedback):
        if verbal_feedback:
            self.problems[problem_id]["feedback_history"].append(verbal_feedback)

    def choose_next_guess(self, problem_id, turn):
        candidates = self.problems[problem_id]["candidate_words"]
        history = self.problems[problem_id]["feedback_history"]

        if not history:
            guess = candidates[0]
            self._log(f"Turn {turn}: Received feedback: None (first turn)")
            self._log(f"Turn {turn}: Guess: {guess}")
            self.guess_history[problem_id].append(guess)
            return guess

        last_feedback = history[-1]
        last_guess = self.guess_history[problem_id][-1]

        prompt = f"""

   You are a strict Wordle feedback converter.

You will ONLY receive:
- A 5-letter guessed word
- Natural language feedback for each letter

You will NEVER be given the correct answer.
You must NEVER try to guess it or infer anything from the guess.

Your one and only job:
Convert the natural language feedback into a 5-letter string using only:
- B = correct letter, correct position
- Y = correct letter, wrong position
- G = letter not in the word or extra duplicate

Rules you must obey:
- Do not guess. Do not think.
- Use ONLY the feedback text.
- If there’s no mention of a letter being correct or misplaced, mark it as G.
- For repeated letters, mark only the correct number of times, starting from left.
- Output MUST be one 5-letter string. No quotes, no extra text, no explanation.

---

Example:

(
  "abped",
  "'a' is in the correct position.  'b' is in the correct position.  'p' is not in the word.  'e' is in the word but in the wrong position.  'd' is in the word but in the wrong position.",
  "BBGYY"
),

(
  "eecee",
  "The first 'e' is in the word but in the wrong position.  The second 'e' is not in the word.  The letter 'c' is in the correct position.  The fourth 'e' is not in the word.  The final 'e' is in the correct position.",
  "YGBGB"
),
("level", "The letter 'l' is in the word but in the wrong position.  The letter 'e' is in the correct position.  The letter 'v' is not in the word.  The second 'e' is not in the word.  The final 'l' is in the word but in the wrong position.",  "YBGGY")

  ('crane', "'c' is not in the word.  'r' is not in the word.  'a' is in the correct position.  'n' is not in the word.  'e' is in the correct position.", 'GGBGB'),
  ('state', "'s' is in the correct position.  't' is not in the word.  'a' is in the correct position.  't' is in the correct position.  'e' is in the correct position.", 'BGBBB'),
  ('level', "'l' is in the word but in the wrong position.  'e' is in the correct position.  'v' is not in the word.  'e' is not in the word.  'l' is in the word but in the wrong position.", 'YBGGY'),
  ('spire', "'s' is not in the word.  'p' is in the word but in the wrong position.  'i' is in the correct position.  'r' is in the word but in the wrong position.  'e' is in the correct position.", 'GYBYB'),
  ('glare', "'g' is in the correct position.  'l' is not in the word.  'a' is in the correct position.  'r' is in the word but in the wrong position.  'e' is in the correct position.", 'BGBYB'),
  ('crate', "'c' is in the word but in the wrong position.  'r' is in the correct position.  'a' is in the correct position.  't' is in the word but in the wrong position.  'e' is in the correct position.", 'YBBYB'),
  ('store', "'s' is in the correct position.  't' is in the correct position.  'o' is in the correct position.  'r' is not in the word.  'e' is in the correct position.", 'BBBGB'),
  ('grave', "'g' is not in the word.  'r' is in the correct position.  'a' is in the correct position.  'v' is in the correct position.  'e' is in the correct position.", 'GBBBB'),
  ('grove', "'g' is in the correct position.  'r' is not in the word.  'o' is in the correct position.  'v' is in the correct position.  'e' is in the correct position.", 'BGBBB'),
  ('shape', "'s' is in the correct position.  'h' is in the correct position.  'a' is in the correct position.  'p' is not in the word.  'e' is in the correct position.", 'BBBGB'),
  ('grace', "'g' is not in the word.  'r' is in the correct position.  'a' is in the correct position.  'c' is in the word but in the wrong position.  'e' is in the correct position.", 'GBBYB'),
  ('blame', "'b' is not in the word.  'l' is in the correct position.  'a' is in the correct position.  'm' is not in the word.  'e' is in the correct position.", 'GBBGB'),
  ('modal', "'m' is in the correct position.  'o' is not in the word.  'd' is in the correct position.  'a' is in the word but in the wrong position.  'l' is not in the word.", 'BGBYG'),
  ('clown', "'c' is in the correct position.  'l' is not in the word.  'o' is in the correct position.  'w' is in the correct position.  'n' is in the correct position.", 'BGBBB'),
  ('spice', "'s' is in the correct position.  'p' is in the correct position.  'i' is in the correct position.  'c' is not in the word.  'e' is in the correct position.", 'BBBGB'),
  ('house', "'h' is not in the word.  'o' is in the correct position.  'u' is in the correct position.  's' is in the correct position.  'e' is in the correct position.", 'GBBBB'),
  ('shone', "'s' is in the correct position.  'h' is in the correct position.  'o' is not in the word.  'n' is in the correct position.  'e' is in the correct position.", 'BBGBB'),
  ('blaze', "'b' is in the correct position.  'l' is in the correct position.  'a' is in the correct position.  'z' is not in the word.  'e' is in the correct position.", 'BBBGB'),
  ('place', "'p' is in the correct position.  'l' is in the correct position.  'a' is in the correct position.  'c' is not in the word.  'e' is in the correct position.", 'BBBGB'),
  ('flake', "'f' is in the correct position.  'l' is in the correct position.  'a' is not in the word.  'k' is in the correct position.  'e' is in the correct position.", 'BBGBB')
]

---

Now process this input:

Guess: {last_guess}  
Feedback:  
{last_feedback}  
→ Output:

Let's think step by step.
        """

        pattern = (
            complete(
                model=self.model,
                prompt=[{"role": "user", "content": prompt}],
                options={"max_tokens": 5, "temperature": 0.0},
                session=self.session,
            )
            .strip()
            .upper()
        )
        import re

        self.snowflake_calls += 1

        self._log(f"Turn {turn}: LLM pattern = {pattern}")

        def match_pattern(word, guess, pattern):
            # word: candidate
            # guess: previous guess
            # pattern: e.g. 'BBGYB'

            # Step 1: Check exact matches for 'B'
            for i in range(5):
                if pattern[i] == "B":
                    if word[i] != guess[i]:
                        return False

            # Step 2: Count how many Y's and G's should be used per letter (to handle duplicates)
            required_counts = {}
            for i in range(5):
                if pattern[i] in ('Y', 'G'):
                    required_counts[guess[i]] = required_counts.get(guess[i], 0) + 1

            # Step 3: Count how many letters actually match for Y/G
            actual_counts = Counter(word)
            for ch, cnt in required_counts.items():
                if actual_counts.get(ch, 0) < cnt:
                    return False

            # Step 4: Check Y/G positions
            seen_counts = {}
            for i in range(5):
                ch = guess[i]
                if pattern[i] == 'Y':
                    if word[i] == ch:
                        return False
                    seen_counts[ch] = seen_counts.get(ch, 0) + 1
                elif pattern[i] == 'G':
                    seen_counts[ch] = seen_counts.get(ch, 0) + 1

            return True


        filtered_candidates = [w for w in candidates if match_pattern(w, last_guess, pattern)]
        filtered_candidates = [w for w in filtered_candidates if w != last_guess]
        if not filtered_candidates:
            filtered_candidates = [w for w in candidates if w != last_guess]

        guess = Solver.choose_entropy_base(filtered_candidates)

        self._log(f"Turn {turn}: Guess: {guess}")
        self.problems[problem_id]["candidate_words"] = filtered_candidates
        self.guess_history[problem_id].append(guess)
        return guess

    @staticmethod
    def get_pattern(guess, ans):
        n = len(guess)
        pattern_ls = [''] * n
        ans_counts = Counter(ans)
        for i in range(n):
            if guess[i] == ans[i]:
                pattern_ls[i] = 'B'
                ans_counts[guess[i]] -= 1
        for i in range(n):
            if pattern_ls[i] == '':
                if ans_counts[guess[i]] > 0:
                    pattern_ls[i] = 'Y'
                    ans_counts[guess[i]] -= 1
                else:
                    pattern_ls[i] = 'G'
        return ''.join(pattern_ls)

    @staticmethod
    def get_entropy(word, candidate_list):
        counts = Counter()
        for ans in candidate_list:
            feedback = Solver.get_pattern(word, ans)
            counts[feedback] += 1
        total = sum(counts.values())
        entropy = 0
        for count in counts.values():
            p = count / total
            entropy -= p * math.log2(p)
        return entropy

    @staticmethod
    def choose_entropy_base(candidate_list):
        max_en = -1
        max_word = None
        for word in candidate_list:
            en = Solver.get_entropy(word, candidate_list)
            if en > max_en:
                max_en = en
                max_word = word
        return max_word

    def _log(self, msg):
        ts = datetime.datetime.now().isoformat()
        self.log_file.write(f"[{ts}] {msg}\n")
        self.log_file.flush()

solver = Solver()

class StudentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length"))
        data = json.loads(self.rfile.read(length))
        if self.path == "/start_problem":
            problem_id = data["problem_id"]
            candidate_words = data["candidate_words"]
            solver.start_problem(problem_id, candidate_words)
            self.send_response(200)
            self.end_headers()
            return
        if self.path == "/guess":
            problem_id = data["problem_id"]
            verbal_feedback = data.get("verbal_feedback")
            turn = data["turn"]
            solver.add_feedback(problem_id, verbal_feedback)
            guess = solver.choose_next_guess(problem_id, turn)
            response = {"guess": guess}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
            return
        self.send_response(404)
        self.end_headers()

def run():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), StudentHandler)
    print(f"Student server running on port {port}...")
    server.serve_forever()

if __name__ == "__main__":
    run()
