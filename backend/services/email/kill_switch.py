"""
Kill switch — auto-pauses all email sending when the failure/bounce rate
spikes, so a broken template or personalization field cannot blast a
large batch before anyone notices.
"""


class KillSwitch:
    """Checks whether sending should be paused based on recent stats."""

    MIN_SAMPLES = 5  # don't judge on tiny samples

    async def is_paused(self, repo, email_config=None) -> tuple:
        """
        Returns (paused: bool, reason: str).
        Paused when bounce+fail rate over the last hour exceeds the
        configured threshold (default 15%).
        """
        from backend.services.config_service import EmailConfig

        if email_config is None:
            email_config = EmailConfig({})

        try:
            stats = await repo.get_email_stats_last_hour()
        except Exception:
            return False, ""

        total = stats.get("total", 0)
        if total < self.MIN_SAMPLES:
            return False, ""

        failures = (stats.get("bounced", 0) or 0) + (stats.get("failed", 0) or 0)
        rate = failures / total * 100.0
        threshold = getattr(email_config, "kill_switch_bounce_threshold", 15)

        if rate > threshold:
            return True, (
                f"kill switch: {failures}/{total} emails failed in the last hour "
                f"({rate:.1f}% > {threshold}%)"
            )
        return False, ""


kill_switch = KillSwitch()