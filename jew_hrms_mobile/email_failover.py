"""Gmail daily-limit failover for outgoing e-mail.

The HRMS mails go out through the Gmail account hrmsjewipl@gmail.com, which Google keeps blocking with
"550 5.4.5 Daily user sending limit exceeded". When that account is blocked the mail is sent through the other
Gmail account (jewipl07@gmail.com) instead - and the other way round if THAT account hits its limit.

Switch (per site, site_config.json):   "jew_smtp_failover": ["HRMS", "JEW"]
  = the names of the Email Accounts that back each other up. Without this key (or with fewer than 2 names) this
  class behaves exactly like the stock Email Queue, so other sites / accounts are untouched.

How: this is the 'Email Queue' document class (hooks.py override_doctype_class). send():
  * mails of an account outside the list are sent exactly as before;
  * otherwise the mail's own account is tried first - unless it was flagged "limited" in the last hour - then the
    other account(s); an account that answers with a sending-limit error is flagged for an hour so the following mails
    do not waste a connection on it; flagged accounts are tried last (so if both are limited we still try);
  * a failed attempt on an earlier account leaves no trace (retry counter / status are restored); only the LAST
    attempt does the normal bookkeeping (retry+1, Error after the retry limit, failure notification).
Gmail itself rewrites the From address to the account that logs in; Reply-To of the mail is kept.
Every failover event is appended to logs/jew_smtp_failover.log.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

import frappe
from frappe.email.doctype.email_queue.email_queue import EmailQueue

LIMIT_COOLDOWN_SECONDS = 3600
_LOG = None


def _log(msg):
	global _LOG
	try:
		if _LOG is None:
			_LOG = logging.getLogger("jew_smtp_failover")
			_LOG.setLevel(logging.INFO)
			_LOG.propagate = False
			path = os.path.join(frappe.utils.get_bench_path(), "logs", "jew_smtp_failover.log")
			h = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3)
			h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
			_LOG.addHandler(h)
		_LOG.info("[%s] %s", frappe.local.site, msg)
	except Exception:
		pass


def _cache():
	c = frappe.cache
	return c() if callable(c) else c


def _pool():
	pool = frappe.conf.get("jew_smtp_failover") or []
	if isinstance(pool, (list, tuple)):
		pool = [p for p in pool if p]
		if len(pool) >= 2:
			return list(pool)
	return []


def _is_limit_error(text):
	t = (text or "").lower()
	return "5.4.5" in t or "sending limit" in t or "rate limit" in t or "quota" in t


def _is_limited(account):
	try:
		return bool(_cache().get_value("jew_smtp_limited:" + account))
	except Exception:
		return False


def _flag_limited(account):
	try:
		_cache().set_value("jew_smtp_limited:" + account, 1, expires_in_sec=LIMIT_COOLDOWN_SECONDS)
	except Exception:
		pass


def attempt_order(pool, natural):
	"""Own account first, then the others; accounts flagged as limited go to the back."""
	ordered = [natural] + [p for p in pool if p != natural]
	return [a for a in ordered if not _is_limited(a)] + [a for a in ordered if _is_limited(a)]


class JEWEmailQueue(EmailQueue):
	def send(self, smtp_server_instance=None, frappe_mail_client=None, force_send=False):
		order, natural = None, None
		try:
			pool = _pool()
			if pool:
				acct = self.get_email_account()
				natural = acct.name if acct else None
				if natural and natural in pool and (self.can_send_now() or force_send):
					order = attempt_order(pool, natural)
		except Exception:
			order = None
		if not order:
			# feature off / account outside the pair / nothing to send: exactly the stock behaviour
			return super().send(smtp_server_instance, frappe_mail_client, force_send)

		real_retry = int(self.retry or 0)
		last_index = len(order) - 1
		for index, account in enumerate(order):
			is_last = index == last_index
			account_doc = frappe.get_cached_doc("Email Account", account)
			self.email_account = account
			if account_doc.email_id:
				self.sender = account_doc.email_id  # SMTP envelope sender; Gmail rewrites From to the login anyway
			# A failed attempt on a non-last account must not count as a retry (or trigger the failure notification).
			self.retry = real_retry if is_last else 0
			try:
				super().send(smtp_server_instance if (index == 0 and account == natural) else None, frappe_mail_client, force_send)
				if account != natural:
					self.update_db(error="")
					_log("{0}: sent via {1} (own account {2} unavailable)".format(self.name, account, natural))
				return
			except Exception:
				text = frappe.get_traceback()
				limited = _is_limit_error(text)
				if limited:
					_flag_limited(account)
				_log("{0}: attempt via {1} failed ({2}){3}".format(
					self.name, account, "sending limit" if limited else "other error", "" if is_last else " - trying next account"))
				if is_last:
					raise
				# undo the bookkeeping of the failed attempt and carry on with the next account
				self.reload()
				self.update_db(status=self.status if self.status in ("Not Sent", "Partially Sent") else "Not Sent", retry=real_retry)
