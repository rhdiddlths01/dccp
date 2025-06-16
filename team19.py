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
You are a Wordle feedback pattern converter.

Your ONLY job is to convert a 5-letter word guess and its natural language feedback into a pattern of exactly 5 characters using:
- B = correct letter in correct position  
- Y = correct letter in wrong position  
- G = letter is not in the word or extra duplicate

Strict Rules:
- Do NOT guess. Do NOT infer. Use ONLY what the feedback says.
- If no information is given about a letter, mark it as G.
- If letters repeat, mark only as many B/Y as mentioned, starting from left.
- The result must be exactly 5 characters, using only B, Y, or G.
- Return just the pattern. No comments, no quotes, no explanation.

---
Examples:
Guess: grape  
Feedback: 'g' is not in the word.  'r' is not in the word.  'a' is in the correct position.  'p' is not in the word.  'e' is not in the word.  
→ Output: GGBGG

Guess: place  
Feedback: 'p' is not in the word.  'l' is not in the word.  'a' is in the correct position.  'c' is not in the word.  'e' is in the correct position.  
→ Output: GGBGB

Guess: crate  
Feedback: 'c' is in the word but in the wrong position.  'r' is in the correct position.  'a' is in the correct position.  't' is in the word but in the wrong position.  'e' is in the correct position.  
→ Output: YBBYB

Guess: level  
Feedback: The letter 'l' is in the word but in the wrong position.  The letter 'e' is in the correct position.  The letter 'v' is not in the word.  The second 'e' is not in the word.  The final 'l' is in the word but in the wrong position.  
→ Output: YBGGY

---

Now convert the following input into a 5-letter pattern.  
Guess: {last_guess}  
Feedback: {last_feedback}  
→ Output:
        """

        VALID_CHARS = {"B", "Y", "G"}

        def get_valid_pattern(prompt, max_retries=3):
            for attempt in range(max_retries):
                pattern = (
                    complete(
                        model=self.model,
                        prompt=[{"role": "user", "content": prompt}],
                        options={"max_tokens": 7, "temperature": 0.0},
                        session=self.session,
                    )
                    .strip()
                    .upper()
                    .replace(" ", "")[:5]
                )

                if len(pattern) == 5 and all(c in VALID_CHARS for c in pattern):
                    return pattern

                print(f"⚠️ Invalid pattern received: {pattern} (attempt {attempt + 1})")

            raise ValueError("❌ Failed to get a valid pattern from LLM after multiple retries.")

        pattern = get_valid_pattern(prompt)
        

        self.snowflake_calls += 1

        self._log(f"Turn {turn}: LLM pattern = {pattern}")


        def match_pattern(word, guess, pattern):
            # Step 1: Check exact matches for 'B'
            for i in range(5):
                if pattern[i] == "B" and word[i] != guess[i]:
                    return False

            # Step 2: Build counters to manage letter occurrences
            guess_counter = Counter()
            word_counter = Counter(word)

            # Step 3: First, handle 'B' positions to reduce counts
            for i in range(5):
                if pattern[i] == "B":
                    ch = guess[i]
                    guess_counter[ch] += 1
                    word_counter[ch] -= 1  # use up one instance of that letter in word

            # Step 4: Handle 'Y' positions
            for i in range(5):
                if pattern[i] == "Y":
                    ch = guess[i]
                    if word[i] == ch:  # same position match is invalid for Y
                        return False
                    if word_counter[ch] <= 0:  # no more of that letter left
                        return False
                    word_counter[ch] -= 1  # use up one instance
                    guess_counter[ch] += 1

            # Step 5: Handle 'G' positions
            for i in range(5):
                if pattern[i] == "G":
                    ch = guess[i]
                    if word[i] == ch:  # can't be same letter in same position
                        return False
                    # 'G' means the letter is not in the word (or already used up)
                    if word_counter[ch] > 0:
                        return False

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