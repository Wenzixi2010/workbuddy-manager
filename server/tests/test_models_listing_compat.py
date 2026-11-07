"""面板 `/v1/models` 的能力字段兜底（issue #211）。

#156 只在上游侧补了 `modalities` / `supported_reasoning_levels`；面板是独立进程，
随包上游可能比面板旧，于是下游客户端经面板拿到的清单仍缺字段，整条模型被跳过。

这里锁三条：
  1. 多模态模型的 `modalities.input` 含 image；纯文本模型也有 `modalities`（只含 text）
     —— 缺整个字段才是 #211 的现场；
  2. `supported_reasoning_levels` 由 `reasoning_supported_efforts` 派生并补描述，
     默认档只来自实际支持的上游默认值，没有档位就不编造；
  3. **只增不改**：上游已经给了的 effort / description、其它顶层键与未知条目保留。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.routers.gateway import _listing_compat_models  # noqa: E402


class ListingCompatTest(unittest.TestCase):
    def test_multimodal_and_reasoning(self) -> None:
        payload = {'object': 'list', 'data': [{
            'id': 'global:deepseek-v4.1-flash',
            'supports_images': True,
            'reasoning_supported_efforts': ['low', 'high', 'max'],
            'reasoning_default_effort': 'high',
        }]}
        out = _listing_compat_models(payload)
        entry = out['data'][0]
        self.assertEqual(entry['modalities'], {'input': ['text', 'image']})
        self.assertEqual([level['effort'] for level in entry['supported_reasoning_levels']],
                         ['low', 'high', 'max'])
        self.assertTrue(all(isinstance(level['description'], str) and level['description']
                            for level in entry['supported_reasoning_levels']))
        self.assertTrue(entry['supports_reasoning'])
        self.assertEqual(entry['default_reasoning_level'], 'high')
        self.assertEqual(entry['default_reasoning_effort'], 'high')
        # 原有字段与顶层结构不变
        self.assertTrue(entry['supports_images'])
        self.assertEqual(entry['reasoning_supported_efforts'], ['low', 'high', 'max'])
        self.assertEqual(out['object'], 'list')

    def test_text_only_model_still_gets_modalities(self) -> None:
        """纯文本模型也要有 modalities.input —— 缺字段会让客户端整条跳过。"""
        out = _listing_compat_models({'data': [{'id': 'cn:glm-5.2'}]})
        self.assertEqual(out['data'][0]['modalities'], {'input': ['text']})
        self.assertNotIn('supported_reasoning_levels', out['data'][0])
        self.assertNotIn('supports_reasoning', out['data'][0])

    def test_upstream_fields_are_not_overwritten(self) -> None:
        """上游已经给了的字段原样保留（新上游自己会补，别把它的口径改掉）。"""
        entry = {
            'id': 'x', 'supports_images': True,
            'modalities': {'input': ['text']},
            'supported_reasoning_levels': [{'effort': 'medium', 'description': 'From upstream'}],
            'supports_reasoning': False,
            'reasoning_supported_efforts': ['high'],
        }
        out = _listing_compat_models({'data': [entry]})
        got = out['data'][0]
        self.assertEqual(got['modalities'], {'input': ['text']})
        self.assertEqual(got['supported_reasoning_levels'],
                         [{'effort': 'medium', 'description': 'From upstream'}])
        self.assertFalse(got['supports_reasoning'])

    def test_missing_description_is_added_without_changing_existing_levels(self) -> None:
        entry = {'id': 'm', 'supported_reasoning_levels': [
            {'effort': 'high', 'vendor_hint': 'keep'},
            {'effort': 'ultra', 'description': 'Vendor ultra'},
        ], 'reasoning_supported_efforts': ['low', 'medium']}
        got = _listing_compat_models({'data': [entry]})['data'][0]
        self.assertEqual([level['effort'] for level in got['supported_reasoning_levels']],
                         ['high', 'ultra'])
        self.assertTrue(got['supported_reasoning_levels'][0]['description'])
        self.assertEqual(got['supported_reasoning_levels'][0]['vendor_hint'], 'keep')
        self.assertEqual(got['supported_reasoning_levels'][1]['description'], 'Vendor ultra')
        self.assertNotIn('description', entry['supported_reasoning_levels'][0],
                         '补字段不能修改上游响应对象')

    def test_unsupported_or_missing_default_is_not_invented(self) -> None:
        for default in (None, '', 'medium'):
            with self.subTest(default=default):
                entry = {'id': 'm', 'reasoning_supported_efforts': ['high', 'max']}
                if default is not None:
                    entry['reasoning_default_effort'] = default
                got = _listing_compat_models({'data': [entry]})['data'][0]
                self.assertNotIn('default_reasoning_level', got)
                self.assertNotIn('default_reasoning_effort', got)

    def test_reasoning_flag_alone_does_not_invent_levels(self) -> None:
        got = _listing_compat_models({'data': [{
            'id': 'deepseek-r1', 'supports_reasoning': True,
            'reasoning_default_effort': 'high',
        }]})['data'][0]
        self.assertNotIn('supported_reasoning_levels', got)
        self.assertNotIn('default_reasoning_level', got)
        self.assertNotIn('default_reasoning_effort', got)

    def test_native_default_names_fill_only_the_missing_compat_field(self) -> None:
        for field in ('default_reasoning_level', 'default_reasoning_effort'):
            with self.subTest(field=field):
                entry = {'id': 'm', 'reasoning_supported_efforts': ['low', 'high'],
                         field: 'low'}
                got = _listing_compat_models({'data': [entry]})['data'][0]
                self.assertEqual(got['default_reasoning_level'], 'low')
                self.assertEqual(got['default_reasoning_effort'], 'low')
                self.assertEqual(entry, {'id': 'm', 'reasoning_supported_efforts': ['low', 'high'],
                                         field: 'low'})

    def test_unknown_shapes_pass_through(self) -> None:
        self.assertEqual(_listing_compat_models('not a dict'), 'not a dict')
        self.assertEqual(_listing_compat_models({'error': 'x'}), {'error': 'x'})
        # 缺 id 的条目（认不出是什么）原样保留，不臆造能力字段
        mixed = {'data': [{'no_id': True}, {'id': 'm'}]}
        out = _listing_compat_models(mixed)
        self.assertEqual(out['data'][0], {'no_id': True})
        self.assertIn('modalities', out['data'][1])


if __name__ == '__main__':
    unittest.main()
