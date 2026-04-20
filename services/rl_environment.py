"""
강화학습 주식 거래 환경 (gymnasium)

State  : [피처벡터(stage 기반) + holding + holding_return + days_held]
Action : 0=HOLD, 1=BUY, 2=SELL
Reward : 보유 중 일일 수익률 / 매도 시 실현 수익률 / 잘못된 액션 패널티
"""
import numpy as np
import gymnasium as gym
from gymnasium import spaces


class StockTradingEnv(gym.Env):
    """
    episodes_data: list of {"ticker": str, "features": np.array (T, F), "prices": np.array (T,)}
    각 에피소드 = 한 종목의 전체 거래 기간
    reset() 호출 시 랜덤 에피소드 선택
    """

    metadata = {"render_modes": []}

    def __init__(self, episodes_data: list[dict]):
        super().__init__()

        if not episodes_data:
            raise ValueError("episodes_data가 비어 있습니다")

        self.episodes_data = episodes_data
        self.n_features = episodes_data[0]["features"].shape[1]

        # observation: 피처 + [holding_flag, holding_return, days_held]
        n_obs = self.n_features + 3
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(n_obs,), dtype=np.float32
        )
        # 0=HOLD, 1=BUY, 2=SELL
        self.action_space = spaces.Discrete(3)

        self.features: np.ndarray | None = None
        self.prices: np.ndarray | None = None
        self.current_step = 0
        self.holding = False
        self.buy_price = 0.0
        self.holding_days = 0

    # ── 내부 헬퍼 ────────────────────────────────────────────

    def _get_obs(self) -> np.ndarray:
        feat = self.features[self.current_step].astype(np.float32)
        holding_flag = 1.0 if self.holding else 0.0
        holding_return = 0.0
        if self.holding and self.buy_price > 0:
            holding_return = (float(self.prices[self.current_step]) - self.buy_price) / self.buy_price
        return np.append(feat, [holding_flag, holding_return, float(self.holding_days)])

    # ── gym 인터페이스 ────────────────────────────────────────

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)

        idx = self.np_random.integers(0, len(self.episodes_data))
        ep = self.episodes_data[idx]
        self.features = ep["features"]
        self.prices = ep["prices"]
        self.current_step = 0
        self.holding = False
        self.buy_price = 0.0
        self.holding_days = 0

        return self._get_obs(), {}

    @staticmethod
    def _scale(ret: float) -> float:
        """손실 1.5배 패널티 / 이익 0.8배 보상 — 손실 회피 비대칭 보상"""
        return ret * 1.5 if ret < 0 else ret * 0.8

    def step(self, action: int):
        reward = 0.0
        current_price = float(self.prices[self.current_step])

        if action == 1:  # BUY
            if not self.holding:
                self.holding = True
                self.buy_price = current_price
                self.holding_days = 0
            else:
                reward = -0.001  # 이미 보유 중 재매수 패널티

        elif action == 2:  # SELL
            if self.holding:
                ret = (current_price - self.buy_price) / self.buy_price
                reward = self._scale(ret)
                self.holding = False
                self.buy_price = 0.0
                self.holding_days = 0
            else:
                reward = -0.001  # 미보유 매도 패널티

        else:  # HOLD (0)
            if self.holding and self.current_step > 0:
                prev_price = float(self.prices[self.current_step - 1])
                if prev_price > 0:
                    ret = (current_price - prev_price) / prev_price
                    reward = self._scale(ret)

        if self.holding:
            self.holding_days += 1

        self.current_step += 1
        terminated = self.current_step >= len(self.features) - 1

        # 에피소드 종료 시 보유 포지션 강제 청산
        if terminated and self.holding:
            ret = (current_price - self.buy_price) / self.buy_price
            reward += self._scale(ret)
            self.holding = False

        obs = (
            self._get_obs()
            if not terminated
            else np.zeros(self.n_features + 3, dtype=np.float32)
        )
        return obs, reward, terminated, False, {}

    def render(self):
        pass
