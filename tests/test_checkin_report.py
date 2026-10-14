from unicodedata import east_asian_width
from unittest.mock import AsyncMock, MagicMock

import pytest

import checkin
from utils.checkin_report import AccountCheckinResult, CheckinReport, _compact_money, _money, _short_name
from utils.config import AccountConfig
from utils.notify import NotificationKit


def user_info(quota, used=0):
	return {'success': True, 'quota': quota, 'used_quota': used, 'display': f'Balance: ${quota}'}


def table(card):
	return next(element for element in card['body']['elements'] if element['tag'] == 'table')


def test_reward_includes_concurrent_usage():
	result = AccountCheckinResult.from_user_info('主账号', 'anyrouter', True, user_info(100, 20), user_info(123, 22))
	assert result.reward == 25
	assert result.balance == 123


@pytest.mark.parametrize('before,after', [(None, user_info(125)), (user_info(100), None)])
def test_missing_snapshot_is_unknown_reward(before, after):
	result = AccountCheckinResult.from_user_info('账号', 'anyrouter', True, before, after)
	assert result.reward is None
	card = CheckinReport('2026-10-14 08:00:00', [result]).build_feishu_card('AnyRouter')
	assert table(card)['rows'][0]['status'] == [{'text': '成功', 'color': 'green'}]
	assert '仅汇总已知数据' in card['body']['elements'][0]['content']
	assert result.balance == (125 if after else None)


def test_failed_checkin_keeps_balance_but_not_reward():
	result = AccountCheckinResult.from_user_info('账号', 'anyrouter', False, user_info(100), user_info(125))
	assert result.balance == 125
	assert result.reward is None
	assert result.error == '签到失败，请查看运行日志'


def test_mixed_results_use_native_table_and_plain_text_errors():
	name = '<at id=all></at> **主账号**'
	error = '<text_tag color=red>HTTP 401</text_tag>'
	report = CheckinReport(
		'2026-10-14 08:00:00',
		[
			AccountCheckinResult(name, 'anyrouter', True, 125, 25),
			AccountCheckinResult('备用', 'agentrouter', True, 50, 0),
			AccountCheckinResult('失效', 'anyrouter', False, error=error),
		],
	)
	card = report.build_feishu_card('AnyRouter Check-in Alert')
	assert card['schema'] == '2.0'
	assert card['header']['template'] == 'orange'
	assert [tag['text']['content'] for tag in card['header']['text_tag_list']] == ['成功 2', '失败 1']
	report_table = table(card)
	assert report_table['page_size'] == 10
	assert [column['data_type'] for column in report_table['columns']] == ['text', 'options', 'text']
	assert [column['display_name'] for column in report_table['columns']] == ['账号', '结果', '余额']
	assert [column['width'] for column in report_table['columns']] == ['34%', '33%', '33%']
	assert report_table['rows'][0]['account'] == f'1 {_short_name(name)}'
	assert report_table['rows'][0]['status'] == [{'text': '+$25', 'color': 'green'}]
	assert report_table['rows'][1]['status'] == [{'text': '无增', 'color': 'green'}]
	assert report_table['rows'][2]['balance'] == '—'
	assert report_table['rows'][2]['status'] == [{'text': '失败', 'color': 'red'}]
	assert next(element['text'] for element in card['body']['elements'] if element['tag'] == 'div') == {
		'tag': 'plain_text',
		'content': f'3 失效：{error}',
	}
	panel = card['body']['elements'][-1]
	assert panel['tag'] == 'collapsible_panel'
	assert panel['expanded'] is False
	assert panel['elements'][0]['text']['tag'] == 'plain_text'
	assert f'1 {name} · anyrouter' in panel['elements'][0]['text']['content']


@pytest.mark.parametrize('success,color', [(True, 'green'), (False, 'red')])
def test_header_color_matches_result(success, color):
	card = CheckinReport('now', [AccountCheckinResult('账号', 'anyrouter', success)]).build_feishu_card('AnyRouter')
	assert card['header']['template'] == color


def test_pagination_keeps_all_accounts_and_duplicate_names():
	accounts = [AccountCheckinResult('同名账号', f'provider-{i}', True, i, 0) for i in range(15)]
	card = CheckinReport(
		'now', accounts, screenshot_hint='Screenshots saved to checkin_screenshots/'
	).build_feishu_card('AnyRouter')
	assert len(table(card)['rows']) == 15
	assert table(card)['rows'][-1]['account'] == '15 同名账号'
	panel = next(element for element in card['body']['elements'] if element['tag'] == 'collapsible_panel')
	assert '15 同名账号 · provider-14' in panel['elements'][0]['text']['content']
	assert 'checkin_screenshots/' in card['body']['elements'][-1]['text']['content']


@pytest.mark.parametrize(
	'value,signed,expected', [(None, True, '—'), (-1.25, True, '-$1.25'), (0, True, '$0.00'), (25, True, '+$25.00')]
)
def test_currency_format(value, signed, expected):
	assert _money(value, signed) == expected


@pytest.mark.parametrize(
	'name', ['备用账号', '非常非常长的主账号', 'Account 123456789', '中ABC文DEF长账号', '  主\n账号  ']
)
def test_mobile_account_name_is_short_and_full_name_is_preserved(name):
	short_name = _short_name(name)
	assert sum(2 if east_asian_width(char) in ('W', 'F') else 1 for char in short_name) <= 8
	assert '\n' not in short_name
	card = CheckinReport('now', [AccountCheckinResult(name, 'anyrouter', True, 1234567.89, 1234.56)]).build_feishu_card(
		'AnyRouter'
	)
	assert table(card)['rows'][0]['account'] == f'1 {short_name}'
	assert table(card)['rows'][0]['balance'] == '$1.23M'
	panel = next(element for element in card['body']['elements'] if element['tag'] == 'collapsible_panel')
	assert name in panel['elements'][0]['text']['content']
	assert '$1234567.89' in panel['elements'][0]['text']['content']
	assert '+$1234.56' in panel['elements'][0]['text']['content']


@pytest.mark.parametrize(
	'value,expected',
	[
		(None, '—'),
		(25, '+$25'),
		(1.25, '+$1.25'),
		(1000, '+$1K'),
		(1000000, '+$1M'),
		(1000000000, '+$1B'),
		(-12.5, '-$12.5'),
	],
)
def test_mobile_money_is_compact(value, expected):
	assert _compact_money(value, signed=True) == expected


def test_only_feishu_receives_structured_report(monkeypatch):
	kit = NotificationKit()
	report = CheckinReport('now', [AccountCheckinResult('账号', 'anyrouter', True, 100, 25)])
	methods = {}
	for name in [
		'send_email',
		'send_pushplus',
		'send_serverPush',
		'send_dingtalk',
		'send_feishu',
		'send_wecom',
		'send_gotify',
		'send_telegram',
		'send_bark',
	]:
		methods[name] = MagicMock()
		monkeypatch.setattr(kit, name, methods[name])
	kit.push_message('title', 'original text', feishu_report=report)
	methods['send_feishu'].assert_called_once_with('title', 'original text', report)
	methods['send_email'].assert_called_once_with('title', 'original text', 'text')
	for name, mock in methods.items():
		if name not in ('send_email', 'send_feishu'):
			mock.assert_called_once_with('title', 'original text')


@pytest.mark.asyncio
@pytest.mark.parametrize(
	'case', ['changed', 'unchanged', 'failed', 'missing_before', 'all_missing_before', 'exception']
)
async def test_main_reports_every_account_and_preserves_notification_rules(monkeypatch, case):
	accounts = [
		AccountConfig.from_dict({'name': '主', 'api_user': '1', 'cookies': {'session': 'test'}}, 0),
		AccountConfig.from_dict({'name': '备用', 'api_user': '2', 'cookies': {'session': 'test'}}, 1),
	]
	first_before = None if case in ('missing_before', 'all_missing_before') else user_info(100)
	first_after = user_info(125 if case == 'changed' else 100)
	results = [(True, first_before, first_after), (case != 'failed', user_info(50), user_info(50))]
	if case == 'all_missing_before':
		results[1] = (True, None, user_info(50))
	if case == 'exception':
		results[1] = RuntimeError('browser unavailable')
	monkeypatch.setattr(checkin, 'check_in_account', AsyncMock(side_effect=results))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: MagicMock(providers={}))
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	initial_hash = (
		None
		if case in ('missing_before', 'all_missing_before')
		else checkin.generate_balance_hash(
			{'account_1': {'quota': 100, 'used': 0}, 'account_2': {'quota': 50, 'used': 0}}
		)
	)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: initial_hash)
	monkeypatch.setattr(checkin, 'save_balance_hash', MagicMock())
	push = MagicMock()
	monkeypatch.setattr(checkin.notify, 'push_message', push)
	with pytest.raises(SystemExit) as exit_info:
		await checkin.main()
	assert exit_info.value.code == 0
	if case == 'unchanged':
		push.assert_not_called()
	else:
		push.assert_called_once()
		report = push.call_args.kwargs['feishu_report']
		assert [result.name for result in report.accounts] == ['主', '备用']
		assert report.accounts[1].success == (case not in ('failed', 'exception'))
		assert len(table(report.build_feishu_card('AnyRouter'))['rows']) == 2
		if case in ('missing_before', 'all_missing_before'):
			assert report.accounts[0].reward is None
		if case == 'exception':
			assert report.accounts[1].error == 'browser unavailable'
