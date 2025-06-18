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
import random

load_dotenv()
WORD_LIST = [line.strip() for line in open("words.txt") if len(line.strip()) == 5]

# 샘플 few-shot 예시
few_shot_examples = [
    {
        "guess": "cares",
        "feedback": "The letters 'a' and 'e' are in their correct spots. 'c', 'r', and 's' do not appear in the word.",
        "output": "02200", 
        "explanation": "the first digit('c') is 0 because 'c', 'r', and 's' do not appear in the word. the second digit('a') is 2 because The letters 'a' and 'e' are in their correct spots. the third digit('r') is 2 because 'c', 'r', and 's' do not appear in the word. the fourth digit('e') is 0 because The letters 'a' and 'e' are in their correct spots. the fifth and last digit('s') is 2 'c', 'r', and 's' do not appear in the word"
    },
    {
        "guess": "slate",
        "feedback": "'l' and 'e' are somewhere in the word but misplaced. 't', 's', and 'a' are not in the word.",
        "output": "01001",
        "explanation": "the first digit('s') is 0 because 't', 's', and 'a' are not in the word. the second digit('l') is 1 because 'l' and 'e' are somewhere in the word but misplaced. the third digit('a') is 0 beacuse 't', 's', and 'a' are not in the word. the fourth digit('t') is 0 because The letters 'a' and 'e' are in their correct spots. the fifth and last digit('e') is 1 beacuse 'l' and 'e' are somewhere in the word but misplaced."
    },
    {
        "guess": "abide",
        "feedback": "Only 'b' is in the right position. 'a' is in the word. Others aren't in the target.",
        "output": "12000",
        "explanation":"the first digit('a') is 1 because Only 'b' is in the right position. 'a' is in the word. the second digit('b') is 2 because Only 'b' is in the right position. the third digit('i') is 0 beacuse Others(excluding 'b' and 'a') aren't in the target. the fourth digit('d') is 0 because Others(excluding 'b' and 'a') aren't in the target. the fifth and last digit('e') is 0 beacuse Others(excluding 'b' and 'a') aren't in the target."
    },
    {
        "guess": "crane",
        "feedback": "All letters are wrong except 'a', which is in the correct spot.",
        "output": "00200",
        "explanation":"the first digit('c') is 0 because All letters are wrong except 'a'. the second digit('r') is 0 because All letters are wrong except 'a'. the third digit('a') is 2 beacuse All letters are wrong except 'a', which is in the correct spot. the fourth digit('n') is 0 because All letters are wrong except 'a'. the fifth and last digit('e') is 0 beacuse All letters are wrong except 'a'."
    }
]

def normalize_feedback(feedback: str) -> str:
    feedback = feedback.lower()
    feedback = feedback.replace("not in the word", "absent")
    feedback = re.sub(r"correct.*position", "correct", feedback)
    feedback = re.sub(r"right.*spot", "correct", feedback)
    feedback = re.sub(r"wrong.*position", "misplaced", feedback)
    feedback = feedback.replace("somewhere else", "misplaced")
    return feedback

def build_prompt(guess: str, verbal_feedback: str, few_shot_data: list) -> str:
    samples = random.sample(few_shot_data, k=min(4, len(few_shot_data)))
    prompt = "You are a Wordle feedback interpreter.\n\n"
    prompt += "You will be given:\n- A 5-letter guess word\n- Natural language feedback describing the correctness of each letter in that guess\n\n"
    prompt += "Your task:\n- Output a 5-letter feedback code using only the characters: 0, 1, 2\n\n"
    prompt += "Legend:\n- 2: letter is in the correct position\n- 1: letter is in the word but in the wrong position\n- 0: letter is not in the word at all\n\n"
    prompt += "Examples:\n"

    for ex in samples:
        prompt += f"Guess: {ex['guess']}\nFeedback: {ex['feedback']}\nOutput: {ex['output']}\n\n"
    
    prompt += (
    "Please format your response like this:\n"
    'For the guess "GUESS":\n'
    "- 'x' is {correct/misplaced/absent} ({2/1/0})\n"
    "- ...\n"
    "Therefore, the feedback code is:\n"
    "01220\n\n"

    f"Guess: {guess}\nFeedback: {verbal_feedback}\n"
    "Output (must be exactly 5 letters of 0/1/2 matching the feedback):"
    
    "Example: \n If 's' is misplaced and 'e' is correct, then the last two letters of the code must be: 12 (Not 22!)"
    )
    return prompt


class Solver:
    def __init__(self):
        self.session = self._init_snowflake()
        self.model = "claude-3-5-sonnet"
        self.problems = {}
        self.snowflake_calls = 0
        self.log_file = open("run.log", "a")
        self.original_wordlist = WORD_LIST
        atexit.register(self.cleanup)
        
        # 최적화된 시작 단어들 (정보량이 높은 순서)
        self.optimal_starters = [
            'raise', 'adieu', 'audio', 'arios', 'arose', 'slate', 'crane', 
            'cares', 'tears', 'stare', 'reals', 'rates', 'tales', 'least'
        ]
        


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
        #self.log_file.write(f"[{ts}] {msg}\n")
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
        try:
            normalized = normalize_feedback(verbal_feedback)
            user_prompt = build_prompt(guess, normalized, few_shot_examples)

            response = complete(
                model=self.model,
                prompt=[
                    {"role": "system", "content": "You are a Wordle feedback interpreter."},
                    {"role": "user", "content": user_prompt, }
                ],
                options={"max_tokens": 115, "temperature": 0.0},
                session=self.session
            )

            self._log(f"[RAW LLM RESPONSE] {response}")

            if isinstance(response, str):
                content = response.strip()
            elif isinstance(response, dict):
                content = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            else:
                content = str(response).strip()

            #clean_response = content.upper()
            #self._log(f"[CLEANED RESPONSE] {clean_response}")

            # 정확히 5글자 GYB만 뽑기 (공백/기호 제거 후)
            gyb_exact = re.findall(r"\b[012]{5}\b", content)
            if gyb_exact:
                self._log(f"[EXACT PATTERN MATCH] {gyb_exact[0]}")
                return gyb_exact[0]

            # 문자만 필터링해서 만들어보기
            gyb_only = ''.join(c for c in content if c in "012")
            if len(gyb_only) >= 5:
                self._log(f"[CHAR ONLY MATCH] {gyb_only[:5]}")
                return gyb_only[:5]

            self._log(f"[FALLBACK] Not enough info, defaulting to 00000. Original: {content}")
            return gyb_only.ljust(5, 'G')

        except Exception as e:
            self._log(f"[LLM PARSE ERROR] {e}")
            return "00000"

        
    def parse_feedback(self, guess, verbal_feedback):
        try:
            llm_result = self.parse_feedback_llm(guess, verbal_feedback)
            if self.is_valid_feedback_code(llm_result):
                self._log(f"[LLM FEEDBACK PARSED]: guess = {guess}, parsed_code = {llm_result}")
                return llm_result
            else:
                return self.parse_feedback_rules(guess, verbal_feedback)
        except Exception as e:
            self._log(f"Parse feedback error: {e}")
            return self.parse_feedback_rules(guess, verbal_feedback)

    def is_valid_feedback_code(self, code):
        """피드백 코드가 유효한지 확인"""
        return len(code) == 5 and all(c in '012' for c in code)

    def parse_feedback_rules(self, guess, verbal_feedback):
        """규칙 기반 피드백 해석 (간단 버전, 예외 처리를 위해 존재)"""
        self._log(f"Fallback parsing used: {verbal_feedback}")
        return "00000"  # 아주 간단한 placeholder

    def compute_actual_feedback(self, secret, guess):
        """실제 Wordle 규칙에 따라 피드백 계산 (정확한 구현)"""
        feedback = ['0'] * 5
        secret_chars = list(secret.lower())
        guess_chars = list(guess.lower())

        # 1단계: 정확한 위치 (B) 처리
        for i in range(5):
            if guess_chars[i] == secret_chars[i]:
                feedback[i] = '2'
                secret_chars[i] = None  # 사용됨 표시
                guess_chars[i] = None

        # 2단계: 잘못된 위치 (Y) 처리
        for i in range(5):
            if guess_chars[i] is not None:  # 아직 처리되지 않은 글자
                if guess_chars[i] in secret_chars:
                    feedback[i] = '1'
                    # secret에서 해당 글자 제거 (중복 처리)
                    secret_chars[secret_chars.index(guess_chars[i])] = None

        return ''.join(feedback)

    """피드백에 맞는 후보들만 필터링"""
    def filter_candidates(self, candidates, guess, feedback_code):
        valid_candidates = []
        
        for candidate in candidates:
            try:
                if self.is_word_consistent(candidate, guess, feedback_code):
                    valid_candidates.append(candidate)
            except Exception as e:
                self._log(f"Error checking word {candidate}: {e}")
                continue
        
        return valid_candidates

    def is_word_consistent(self, word, guess, feedback_code):
        """단어가 추측과 피드백에 일치하는지 확인"""
        if len(word) != 5 or len(guess) != 5 or len(feedback_code) != 5:
            return False

        # 실제 Wordle 규칙에 따라 피드백을 재계산
        actual_feedback = self.compute_actual_feedback(word, guess)
        return actual_feedback == feedback_code

    """단어의 정보 획득량 계산"""
    def calculate_information_gain(self, word, candidates):
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

    """다음 시작 단어 선택 (candidate_words에 존재하는 가장 앞 optimal_starter)"""
    def get_next_starter(self, problem_id):
        data = self.problems[problem_id]
        starter_idx = data["starter_index"]

        # optimal_starters 순회하면서 존재하는 것 중 첫 번째 선택
        for i in range(starter_idx, len(self.optimal_starters)):
            starter = self.optimal_starters[i]
            if starter in data["candidate_words"]:
                data["starter_index"] = i
                return starter

        # 백업: 고유 글자가 많은 단어 선택
        return max(data["candidate_words"][:20], key=lambda w: len(set(w)), default=data["candidate_words"][0])

    def special_guess(self, problem_id):
        data = self.problems[problem_id]
        candidates = data["candidate_words"]
        guesses = data["guess_history"]

        if len(candidates) <= 3:
            return None
        
        possible_letter_sets = [set(word[i] for word in candidates) for i in range(5)]

        diverse_letters = set()
        common_letters = []
        count = 0
        for letter_set in possible_letter_sets:
            if len(letter_set) > 1:
                diverse_letters.update(letter_set)
                count += 1
            else:
                common_letters.append(list(letter_set)[0])
        
        if count > 3:
            return None
        diverse_letters_ls = list(diverse_letters)
        diverse_letters_list = [l for l in diverse_letters_ls if l not in common_letters]
        if len(diverse_letters_list) < 4:
            diverse_letters_list = diverse_letters_ls
        diverse_letters_ = diverse_letters_list*((5//len(diverse_letters_list)+1))
        return "".join(diverse_letters_[:5])

        

    def choose_next_guess(self, problem_id, turn):
        data = self.problems[problem_id]
        candidates = data["candidate_words"]
        history = data["feedback_history"]
        guesses = data["guess_history"]

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

            # 후보가 2개인 경우: 필터링 생략, 방금 단어만 제거
            if len(candidates) == 2:
                filtered_candidates = [w for w in candidates if w != last_guess]
                self._log(f"[SKIP FILTERING] 2 candidates remain, removed last guess '{last_guess}', remaining: {filtered_candidates}")
            else:
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

                if len(filtered_candidates) == 1:
                    guess = filtered_candidates[0]
                    guesses.append(guess)
                    self._log(f"[FINAL GUESS] Only one candidate left: {guess}")
                    return guess
                
                if len(filtered_candidates) == 2:
                    # 그냥 앞에 있는 후보를 고름
                    guess = filtered_candidates[0]
                    guesses.append(guess)
                    self._log(f"[TWO CANDIDATES] Skipping special guess, choosing: {guess}")
                    return guess
                
            if self.special_guess(problem_id):
                    special_guess = self.special_guess(problem_id)
                    guesses.append(special_guess)
                    self._log(f"[SPECIAL GUESS] Using special guess: {special_guess}")
                    return special_guess

            
            # 다음 추측 선택
            guess = self.select_best_guess(filtered_candidates)
            
            if guess is None or guess in guesses:
                # 사용하지 않은 후보 중 선택
                for candidate in filtered_candidates:
                    if candidate not in guesses:
                        guess = candidate
                        break
                else:
                    guess = "error"
            
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
    print(f"Student server running on port {port}…")
    server.serve_forever()

if __name__ == "__main__":
    run()