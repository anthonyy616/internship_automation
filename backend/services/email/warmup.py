"""
Warm-up ramp — ramps sending volume up gradually instead of blasting the
daily cap on day one, protecting the sender reputation.

Day 1: warmup_increment emails (default 5)
Day 2: 2x, ... capped at the daily cap.
"""


class Warmup:
    """Computes the effective daily sending cap from the warm-up schedule."""

    def today_cap(self, email_config) -> int:
        return email_config.effective_daily_cap

    async def over_warmup_cap(self, repo, email_config) -> tuple:
        """
        Returns (over: bool, sent_today: int, cap: int).
        """
        cap = self.today_cap(email_config)
        try:
            sent_today = await repo.get_today_email_count()
        except Exception:
            sent_today = 0
        return sent_today >= cap, sent_today, cap


warmup = Warmup()