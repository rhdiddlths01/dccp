import atexit
import datetime
import json
import os

from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from snowflake.snowpark import Session
from snowflake.cortex import complete

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
            guess = candidates[0]  #첫 추측 단어
            self._log(f"Turn {turn}: Received feedback: None (first turn)")
            self._log(f"Turn {turn}: Guess: {guess}")
            self.guess_history[problem_id].append(guess)
            return guess

        last_feedback = history[-1]
        last_guess = self.guess_history[problem_id][-1]

        prompt = f"""
            You are a strict parser converting Wordle feedback into a 5-letter code.

            The guess word is: "{last_guess}"
            The natural language feedback is: "{last_feedback}"

            Rules:
            - Return exactly 5 characters.
            - Use only:
            - G = correct letter, correct position
            - Y = correct letter, wrong position
            - B = letter not in the word
            - No other letters or words are allowed.
            - Output format must be EXACTLY like: GBYBG

            ❗Only return the 5-letter string like "BBGGY" — no explanation, no quotation marks, no punctuation.

            If you cannot parse the input, return "BBBBB".

                """.strip()

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

        self.snowflake_calls += 1

        self._log(f"Turn {turn}: LLM pattern = {pattern}")

        # 패턴에 맞는 단어 리스트 새로 만들기
        from collections import Counter

        def match_pattern(word, guess, pattern):
            for i in range(5):
                if pattern[i] == "G":
                    if word[i] != guess[i]:
                        return False
                elif pattern[i] == "Y":
                    if guess[i] not in word or word[i] == guess[i]:
                        return False
                elif pattern[i] == "B":
                    if guess[i] in word:
                        return False
            return True


        filtered_candidates = []
        for word in candidates:
            if match_pattern(word, last_guess, pattern):
                filtered_candidates.append(word)

        if not filtered_candidates:
            filtered_candidates = candidates  # fallback to avoid empty list

        #############################
        #filtered_candidates라는 조건 만족 단어 리스트에서 다음 추측 뭘로 할 지 짜기
        from collections import Counter

        # 후보군에 포함된 알파벳들의 빈도 계산
        all_chars = ''.join(filtered_candidates)
        freq = Counter(all_chars)

        # 각 단어의 점수를 계산: 중복 없는 알파벳만 사용
        def score(word):
            return sum(freq[c] for c in set(word))

        # 점수가 가장 높은 단어 선택
        guess = max(filtered_candidates, key=score) if filtered_candidates else candidates[0]

        ##############################

        # 마지막으로 추측하기

        self._log(f"Turn {turn}: Guess: {guess}")
        self.problems[problem_id]["candidate_words"] = filtered_candidates
        self.guess_history[problem_id].append(guess)
        return guess

    def _log(self, msg):
        ts = datetime.datetime.now().isoformat()
        self.log_file.write(f"[{ts}] {msg}\n")
        self.log_file.flush()


solver = Solver()


class StudentHandler(BaseHTTPRequestHandler):
    def do_POST(self):          # POST 요청이 오면 실행
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
    server = HTTPServer(("0.0.0.0", port), StudentHandler) #여기서 실행
    print(f"Student server running on port {port}...")
    server.serve_forever()


if __name__ == "__main__":
    run()
