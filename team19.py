import atexit
import datetime
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from snowflake.snowpark import Session
from snowflake.cortex import complete
from dotenv import load_dotenv
from collections import Counter
import re
import traceback

load_dotenv()

class Solver:
    def __init__(self):
        self.session = self._init_snowflake()
        self.model = "claude-3-5-sonnet"
        self.problems = {}
        self.snowflake_calls = 0
        self.log_file = open("run.log", "a")
        atexit.register(self.cleanup)
        
        # 최적화된 시작 단어들 (정보량이 높은 순서)
        self.optimal_starters = [
            'raise', 'adieu', 'audio', 'arios', 'arose', 'slate', 'crane', 
            'cares', 'tears', 'stare', 'reals', 'rates', 'tales', 'least'
        ]
        
        # 개선된 피드백 파싱 프롬프트
        self.prompt = """You are a Wordle feedback parser. Convert natural language feedback to exactly 5 letters using G, Y, B.

Rules:
- G (Gray): letter is NOT in the word
- Y (Yellow): letter is IN the word but WRONG position  
- B (Black/Green): letter is in CORRECT position

Parse each position of the guess word based on the feedback description.

Examples:
Input: "'r' is not in the word. 'a' is in the correct position. 'i' is not in the word. 's' is not in the word. 'e' is in the word but in the wrong position."
Output: GBGGY

Input: "'h' is in the word but in the wrong position. 'a' is in the correct position. 'l' is not in the word. 'e' is in the correct position. 'd' is in the correct position."
Output: YBGBB

Return ONLY the 5-character code with no explanation."""

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
            self.session.close()
            self.log_file.close()
        except:
            pass

    def _log(self, msg):
        ts = datetime.datetime.now().isoformat()
        print(f"[LOG] {msg}")
        self.log_file.write(f"[{ts}] {msg}\n")
        self.log_file.flush()

    def start_problem(self, problem_id, candidate_words):
        self.problems[problem_id] = {
            "original_candidates": candidate_words.copy(),
            "candidate_words": candidate_words.copy(),
            "guess_history": [],
            "feedback_history": [],
            "starter_index": 0,
            "error_count": 0,
            "max_errors": 3
        }
        self._log(f"=== Problem {problem_id} started with {len(candidate_words)} candidates ===")

    def add_feedback(self, problem_id, verbal_feedback):
        if verbal_feedback:
            self.problems[problem_id]["feedback_history"].append(verbal_feedback)

    def reset_with_new_starter(self, problem_id):
        """오류 발생 시 새로운 시작 단어로 재시작"""
        data = self.problems[problem_id]
        data["starter_index"] += 1
        data["candidate_words"] = data["original_candidates"].copy()
        
        # 이전에 시도한 단어들을 제외
        for old_guess in data["guess_history"]:
            if old_guess in data["candidate_words"]:
                data["candidate_words"].remove(old_guess)
        
        data["feedback_history"] = []
        data["error_count"] = 0  # 에러 카운트 리셋
        
        self._log(f"RESET: Using starter #{data['starter_index']}, candidates: {len(data['candidate_words'])}")

    def parse_feedback_llm(self, guess, verbal_feedback):
        """LLM을 사용한 피드백 파싱 (개선된 버전)"""
        try:
            user_prompt = f"""Guess word: {guess}
Feedback: {verbal_feedback}

Convert to 5-character code (G/Y/B):"""
            
            response = complete(
                model=self.model,
                prompt=[
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": user_prompt}
                ],
                options={"max_tokens": 10, "temperature": 0.0},
                session=self.session
            )
            
            # 응답에서 정확한 GYB 패턴 추출
            clean_response = response.strip().upper()
            
            # 5글자 GYB 패턴 찾기
            gyb_pattern = re.findall(r'[GYB]{5}', clean_response)
            if gyb_pattern:
                return gyb_pattern[0]
            
            # 패턴이 없으면 GYB 문자만 추출
            gyb_chars = ''.join(c for c in clean_response if c in "GYB")
            if len(gyb_chars) >= 5:
                return gyb_chars[:5]
            
            # 부족하면 G로 채움
            return gyb_chars.ljust(5, 'G')
            
        except Exception as e:
            self._log(f"LLM parsing error: {e}")
            return "GGGGG"

    def parse_feedback_rules(self, guess, verbal_feedback):
        """개선된 규칙 기반 피드백 파싱"""
        try:
            feedback = ['G'] * 5
            feedback_lower = verbal_feedback.lower()
            
            # 각 글자별로 피드백 분석
            for i, letter in enumerate(guess.lower()):
                # 해당 글자에 대한 설명 찾기
                letter_patterns = [
                    f"'{letter}' is in the correct position",
                    f"'{letter}' is correct",
                    f"'{letter}' is in the right position"
                ]
                
                wrong_position_patterns = [
                    f"'{letter}' is in the word but in the wrong position",
                    f"'{letter}' is in the word but wrong position",
                    f"'{letter}' is misplaced"
                ]
                
                not_in_word_patterns = [
                    f"'{letter}' is not in the word"
                ]
                
                # 패턴 매칭
                if any(pattern in feedback_lower for pattern in letter_patterns):
                    feedback[i] = 'B'
                elif any(pattern in feedback_lower for pattern in wrong_position_patterns):
                    feedback[i] = 'Y'
                elif any(pattern in feedback_lower for pattern in not_in_word_patterns):
                    feedback[i] = 'G'
            
            return ''.join(feedback)
            
        except Exception as e:
            self._log(f"Rule-based parsing error: {e}")
            return "GGGGG"

    def parse_feedback(self, guess, verbal_feedback):
        """피드백 파싱 (LLM 우선, 규칙 기반 백업)"""
        try:
            # LLM 파싱 시도
            llm_result = self.parse_feedback_llm(guess, verbal_feedback)
            
            # 결과 검증
            if self.is_valid_feedback_code(llm_result):
                self._log(f"Feedback parsing: '{verbal_feedback}' -> '{llm_result}'")
                return llm_result
            else:
                # LLM 결과가 유효하지 않으면 규칙 기반 사용
                rule_result = self.parse_feedback_rules(guess, verbal_feedback)
                self._log(f"LLM failed, using rules: '{verbal_feedback}' -> '{rule_result}'")
                return rule_result
                
        except Exception as e:
            self._log(f"Parse feedback error: {e}")
            return self.parse_feedback_rules(guess, verbal_feedback)

    def is_valid_feedback_code(self, code):
        """피드백 코드가 유효한지 확인"""
        return len(code) == 5 and all(c in 'GYB' for c in code)

    def compute_actual_feedback(self, secret, guess):
        """실제 Wordle 규칙에 따라 피드백 계산 (정확한 구현)"""
        feedback = ['G'] * 5
        secret_chars = list(secret.lower())
        guess_chars = list(guess.lower())

        # 1단계: 정확한 위치 (B) 처리
        for i in range(5):
            if guess_chars[i] == secret_chars[i]:
                feedback[i] = 'B'
                secret_chars[i] = None  # 사용됨 표시
                guess_chars[i] = None

        # 2단계: 잘못된 위치 (Y) 처리
        for i in range(5):
            if guess_chars[i] is not None:  # 아직 처리되지 않은 글자
                if guess_chars[i] in secret_chars:
                    feedback[i] = 'Y'
                    # secret에서 해당 글자 제거 (중복 처리)
                    secret_chars[secret_chars.index(guess_chars[i])] = None

        return ''.join(feedback)

    def is_word_consistent(self, word, guess, feedback_code):
        """단어가 추측과 피드백에 일치하는지 확인"""
        if len(word) != 5 or len(guess) != 5 or len(feedback_code) != 5:
            return False
            
        actual_feedback = self.compute_actual_feedback(word, guess)
        return actual_feedback == feedback_code

    def filter_candidates(self, candidates, guess, feedback_code):
        """피드백에 맞는 후보들만 필터링"""
        valid_candidates = []
        
        for candidate in candidates:
            try:
                if self.is_word_consistent(candidate, guess, feedback_code):
                    valid_candidates.append(candidate)
            except Exception as e:
                self._log(f"Error checking word {candidate}: {e}")
                continue
        
        return valid_candidates

    def calculate_information_gain(self, word, candidates):
        """단어의 정보 획득량 계산"""
        if not candidates or len(candidates) <= 1:
            return 0
        
        feedback_groups = {}
        
        # 각 후보에 대해 이 단어로 추측했을 때의 피드백 계산
        for candidate in candidates:
            feedback = self.compute_actual_feedback(candidate, word)
            feedback_groups[feedback] = feedback_groups.get(feedback, 0) + 1
        
        # 엔트로피 계산
        total = len(candidates)
        entropy = 0
        for count in feedback_groups.values():
            p = count / total
            if p > 0:
                import math
                entropy -= p * math.log2(p)
        
        return entropy

    def select_best_guess(self, candidates):
        """최적의 다음 추측 선택"""
        if not candidates:
            return None
        
        if len(candidates) == 1:
            return candidates[0]
        
        if len(candidates) == 2:
            return candidates[0]
        
        try:
            best_word = None
            best_score = -1
            
            # 성능을 위해 검사할 단어 수 제한
            check_words = candidates[:min(50, len(candidates))]
            
            for word in check_words:
                try:
                    # 정보 이득 계산
                    info_gain = self.calculate_information_gain(word, candidates)
                    
                    # 고유 글자 수 보너스
                    unique_letters = len(set(word.lower()))
                    unique_bonus = unique_letters * 0.1
                    
                    # 총 점수
                    score = info_gain + unique_bonus
                    
                    if score > best_score:
                        best_score = score
                        best_word = word
                        
                except Exception as e:
                    self._log(f"Error calculating score for {word}: {e}")
                    continue
            
            return best_word or candidates[0]
            
        except Exception as e:
            self._log(f"Error in select_best_guess: {e}")
            return candidates[0]

    def get_next_starter(self, problem_id):
        """다음 시작 단어 선택"""
        data = self.problems[problem_id]
        starter_idx = data["starter_index"]
        
        if starter_idx < len(self.optimal_starters):
            starter = self.optimal_starters[starter_idx]
            if starter in data["candidate_words"]:
                return starter
        
        # 백업: 고유 글자가 많은 단어 선택
        return max(data["candidate_words"][:20], key=lambda w: len(set(w)), default=data["candidate_words"][0])

    def choose_next_guess(self, problem_id, turn):
        data = self.problems[problem_id]
        candidates = data["candidate_words"]
        history = data["feedback_history"]
        guesses = data["guess_history"]

        self._log(f"Turn {turn}: {len(candidates)} candidates remaining")

        try:
            # 첫 번째 추측
            if not history:
                guess = self.get_next_starter(problem_id)
                guesses.append(guess)
                self._log(f"First guess: {guess}")
                return guess

            # 이전 피드백 처리
            last_guess = guesses[-1]
            last_feedback = history[-1]
            
            # 피드백 파싱
            feedback_code = self.parse_feedback(last_guess, last_feedback)
            
            # 후보 필터링
            filtered_candidates = self.filter_candidates(candidates, last_guess, feedback_code)
            
            # 필터링 결과 검증
            if not filtered_candidates:
                data["error_count"] += 1
                self._log(f"ERROR: No matching candidates! Error count: {data['error_count']}")
                
                if data["error_count"] >= data["max_errors"]:
                    self._log("Max errors reached, resetting with new starter")
                    self.reset_with_new_starter(problem_id)
                    return self.choose_next_guess(problem_id, turn)
                else:
                    # 덜 엄격한 필터링 시도 또는 원본 후보 사용
                    filtered_candidates = [w for w in data["original_candidates"] 
                                         if w not in guesses][:100]
            
            # 후보 목록 업데이트
            data["candidate_words"] = filtered_candidates
            
            self._log(f"After filtering: {len(filtered_candidates)} candidates")
            if len(filtered_candidates) <= 10:
                self._log(f"Remaining candidates: {filtered_candidates}")
            
            # 다음 추측 선택
            guess = self.select_best_guess(filtered_candidates)
            
            if guess is None or guess in guesses:
                # 사용하지 않은 후보 중 선택
                for candidate in filtered_candidates:
                    if candidate not in guesses:
                        guess = candidate
                        break
                else:
                    raise Exception
            
            guesses.append(guess)
            self._log(f"Next guess: {guess}")
            return guess
            
        except Exception as e:
            self._log(f"CRITICAL ERROR in choose_next_guess: {e}")
            self._log(f"Traceback: {traceback.format_exc()}")
            
            data["error_count"] += 1
            if data["error_count"] >= data["max_errors"]:
                self.reset_with_new_starter(problem_id)
                return self.choose_next_guess(problem_id, turn)
            else:
                return "error"


solver = Solver()

class StudentHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
        
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length"))
            data = json.loads(self.rfile.read(length))

            if self.path == "/start_problem":
                problem_id = data["problem_id"]
                candidates = data["candidate_words"]
                solver.start_problem(problem_id, candidates)
                self.send_response(200)
                self.end_headers()
                return

            elif self.path == "/guess":
                problem_id = data["problem_id"]
                feedback = data.get("verbal_feedback")
                turn = data["turn"]

                solver.add_feedback(problem_id, feedback)
                guess = solver.choose_next_guess(problem_id, turn)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"guess": guess}).encode())
                return

            else:
                self.send_response(404)
                self.end_headers()
                
        except Exception as e:
            solver._log(f"HTTP ERROR: {e}")
            solver._log(f"Traceback: {traceback.format_exc()}")
            self.send_response(500)
            self.end_headers()

def run():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), StudentHandler)
    print(f"Student server running on port {port}...")
    server.serve_forever()

if __name__ == "__main__":
    run()