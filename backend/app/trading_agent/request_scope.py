"""Conservative single-month constraints, independent of model date guesses."""
import calendar
import re
from zoneinfo import ZoneInfo

from .answer_contracts import Limitation


def resolve(text, now):
    # Multi-period comparisons retain the composable planner, not a single-month constraint.
    matches = re.findall(r'(今年|本年|去年|前年|20\d{2}年)\s*(\d{1,2}|[一二三四五六七八九十]{1,3})月', text)
    if len(matches) != 1 or re.search(r'同比|历年|多年|同期|20\d{2}-\d{2}-\d{2}', text):
        return {}
    year_word, month_word = matches[0]
    current = now.astimezone(ZoneInfo('Asia/Shanghai'))
    year = current.year + {'今年': 0, '本年': 0, '去年': -1, '前年': -2}.get(year_word, 0) if not year_word[0].isdigit() else int(year_word[:4])
    months = ['一','二','三','四','五','六','七','八','九','十','十一','十二']
    month = int(month_word) if month_word.isdigit() else months.index(month_word) + 1 if month_word in months else 0
    if not 1 <= month <= 12:
        return {}
    return {'start_date': f'{year:04d}-{month:02d}-01',
            'end_date': f'{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}',
            **({'ports': ['日照']} if '日照' in text else {})}


def check_query(scope, args):
    if not scope:
        return
    filters = args.get('filters') or {}
    ports = [{'日照港': '日照'}.get(p, p) for p in filters.get('ports') or []]
    if (args.get('mode') != 'range' or any(str(args.get(k)) != scope[k] for k in ('start_date', 'end_date'))
            or (scope.get('ports') and ports != scope['ports'])):
        raise ValueError('request_scope_mismatch')


def assess(result, scope, envelopes, failed_tools=()):
    """Reject wrong-scope delivery; never equate these limited checks with full Eval."""
    matching = []
    if scope:
        for envelope in envelopes:
            payload = envelope.payload or {}
            if payload.get('kind') != 'dataset_rows':
                continue
            try:
                check_query(scope, payload.get('selection') or {})
            except ValueError:
                continue
            if envelope.status in {'complete', 'partial'} and payload.get('row_count', 0) > 0:
                matching.append(envelope)
        wrong_year = any(year != scope['start_date'][:4] for year in re.findall(r'(20\d{2})\s*年\s*\d{1,2}\s*月', result.plain_text))
        if wrong_year or not matching:
            text = f"本次应查询 {scope['start_date']} 至 {scope['end_date']}，但未取得符合该范围的可用结果，请重试。"
            return result.model_copy(update={'delivery_status': 'partial', 'body_markdown': text, 'plain_text': text,
                'views': [], 'evidence': [], 'limitations': [Limitation(code='request_scope_unverified', message=text)]})
    if failed_tools and result.delivery_status == 'complete':
        text = '部分查询未完成，本次回答不能视为完整结果。'
        return result.model_copy(update={'delivery_status': 'partial', 'limitations': [*result.limitations, Limitation(code='query_incomplete', message=text)]})
    return result
