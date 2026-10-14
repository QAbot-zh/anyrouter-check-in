"""Structured check-in results and a compact Feishu JSON 2.0 card."""

from dataclasses import dataclass, field
from typing import Any
from unicodedata import east_asian_width

# Public Feishu icon identifier.
FEISHU_EXPAND_ICON = 'down-small-ccm_outlined'


@dataclass
class AccountCheckinResult:
	name: str
	provider: str
	success: bool
	balance: float | None = None
	reward: float | None = None
	error: str | None = None

	@classmethod
	def from_user_info(
		cls, name: str, provider: str, success: bool, before: dict | None, after: dict | None
	) -> 'AccountCheckinResult':
		balance = after['quota'] if after and after.get('success') else None
		reward = None
		if success and balance is not None and after and before and before.get('success'):
			# Include consumption during check-in, matching the existing text report.
			reward = round(balance + after['used_quota'] - before['quota'] - before['used_quota'], 2)
		error = None
		if not success:
			error = after.get('error') if after and not after.get('success') else None
			error = error or '签到失败，请查看运行日志'
		elif balance is None:
			error = (after or {}).get('error') or '签到成功，但未获取到余额'
		return cls(name, provider, success, balance, reward, error)


def _money(value: float | None, signed: bool = False) -> str:
	if value is None:
		return '—'
	if signed and value > 0:
		return f'+${value:.2f}'
	if value < 0:
		return f'-${abs(value):.2f}'
	return f'${value:.2f}'


def _short_name(name: str) -> str:
	"""Limit table labels to four CJK characters or eight narrow characters."""
	name = ' '.join(name.split())
	width = lambda char: 2 if east_asian_width(char) in ('W', 'F') else 1
	if sum(width(char) for char in name) <= 8:
		return name
	result = ''
	for char in name:
		if sum(width(part) for part in result) + width(char) > 7:
			break
		result += char
	return result + '…'


def _compact_money(value: float | None, signed: bool = False) -> str:
	if value is None:
		return '—'
	amount = abs(value)
	suffix = ''
	for threshold, unit in [(1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'K')]:
		if amount >= threshold:
			amount /= threshold
			suffix = unit
			break
	text = f'{amount:.2f}'.rstrip('0').rstrip('.')
	sign = '-' if value < 0 else ('+' if signed and value > 0 else '')
	return f'{sign}${text}{suffix}'


def _status(account: AccountCheckinResult) -> str:
	if not account.success:
		return '失败'
	if account.reward is None:
		return '成功'
	if account.reward == 0:
		return '无增'
	return _compact_money(account.reward, signed=True)


@dataclass
class CheckinReport:
	executed_at: str
	accounts: list[AccountCheckinResult] = field(default_factory=list)
	screenshot_hint: str | None = None

	def build_feishu_card(self, title: str) -> dict[str, Any]:
		"""Use native tables and option tags, never parse the text notification.

		Component reference:
		https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/table
		"""
		success_count = sum(account.success for account in self.accounts)
		failed_count = len(self.accounts) - success_count
		color = 'green' if not failed_count else ('orange' if success_count else 'red')
		known_rewards = [account.reward for account in self.accounts if account.reward is not None]
		unknown_rewards = sum(account.success and account.reward is None for account in self.accounts)
		summary = f'**奖励** {_money(sum(known_rewards), signed=True) if known_rewards else "—"} · {len(self.accounts)} 个账号'
		if unknown_rewards:
			summary += f'\n{unknown_rewards} 个奖励未知，仅汇总已知数据。'
		elements: list[dict[str, Any]] = [{'tag': 'markdown', 'content': summary}]
		if self.accounts:
			elements.append(
				{
					'tag': 'table',
					'page_size': 10,
					'row_height': 'low',
					'columns': [
						{'name': 'account', 'display_name': '账号', 'data_type': 'text', 'width': '34%'},
						{'name': 'status', 'display_name': '结果', 'data_type': 'options', 'width': '33%'},
						{'name': 'balance', 'display_name': '余额', 'data_type': 'text', 'width': '33%'},
					],
					'rows': [
						{
							'account': f'{index + 1} {_short_name(account.name)}',
							'status': [
								{
									'text': _status(account),
									'color': 'green' if account.success else 'red',
								}
							],
							'balance': _compact_money(account.balance),
						}
						for index, account in enumerate(self.accounts)
					],
				}
			)
		issues = [
			f'{index + 1} {_short_name(account.name)}：{account.error}'
			for index, account in enumerate(self.accounts)
			if account.error
		]
		if issues:
			elements.extend(
				[
					{'tag': 'hr'},
					{'tag': 'markdown', 'content': '**异常**'},
					# Names and errors stay plain text so they cannot inject mentions or markup.
					{'tag': 'div', 'text': {'tag': 'plain_text', 'content': '\n'.join(issues)}},
				]
			)
		if self.accounts:
			elements.append(
				{
					'tag': 'collapsible_panel',
					'expanded': False,
					'header': {
						'title': {'tag': 'plain_text', 'content': '明细'},
						'icon': {'tag': 'standard_icon', 'token': FEISHU_EXPAND_ICON, 'size': '16px 16px'},
					},
					'elements': [
						{
							'tag': 'div',
							'text': {
								'tag': 'plain_text',
								'content': '\n\n'.join(
									f'{index + 1} {account.name} · {account.provider}\n'
									f'奖励 {_money(account.reward, signed=True)} · 余额 {_money(account.balance)}'
									for index, account in enumerate(self.accounts)
								),
							},
						}
					],
				}
			)
		if self.screenshot_hint:
			elements.append({'tag': 'div', 'text': {'tag': 'plain_text', 'content': self.screenshot_hint}})
		return {
			'schema': '2.0',
			'config': {'update_multi': True},
			'header': {
				'template': color,
				'title': {
					'tag': 'plain_text',
					'content': 'AnyRouter 签到' if title == 'AnyRouter Check-in Alert' else title,
				},
				'subtitle': {'tag': 'plain_text', 'content': self.executed_at},
				'text_tag_list': [
					{
						'tag': 'text_tag',
						'text': {'tag': 'plain_text', 'content': f'成功 {success_count}'},
						'color': 'green',
					},
					{
						'tag': 'text_tag',
						'text': {'tag': 'plain_text', 'content': f'失败 {failed_count}'},
						'color': 'red' if failed_count else 'neutral',
					},
				],
			},
			'body': {'elements': elements},
		}
