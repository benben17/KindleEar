#!/usr/bin/env python3
# -*- coding:utf-8 -*-
import datetime, json
from test_base import *
from application.back_end.db_models import KeUser, ReaderUser, AppInfo
from application.view.reader import clean_title

class CommercialVipReaderTestCase(BaseTestCase):
    def test_clean_title(self):
        # 验证数字前缀剥离与 HTML/XML 反转义
        self.assertEqual(clean_title("001_Reuters News"), "Reuters News")
        self.assertEqual(clean_title("02_BBC &amp; Bloomberg"), "BBC & Bloomberg")
        self.assertEqual(clean_title("&#39;Breaking News&#39; &quot;World&quot;"), "'Breaking News' \"World\"")
        self.assertEqual(clean_title("Normal Title"), "Normal Title")

    def test_keuser_vip_and_media_quota(self):
        admin = KeUser.get_or_none(KeUser.name == 'admin')
        self.assertTrue(admin.is_vip())
        self.assertEqual(admin.vip_level(), 'admin')

        # 创建一个普通免费用户
        user = KeUser.get_or_none(KeUser.name == 'test_commercial_user')
        if user:
            user.delete_instance()
        user = KeUser(name='test_commercial_user')
        user.passwd_hash = user.hash_text('123456')
        user.save()

        # 初始应为 free
        self.assertFalse(user.is_vip())
        self.assertEqual(user.vip_level(), 'free')

        # 免费用户订阅上限为 2
        saved = user.set_subscribed_media(['Reuters', 'BBC News', 'Bloomberg', 'Wall Street Journal'])
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved, ['Reuters', 'BBC News'])

        # 赋予 VIP
        now = datetime.datetime.now()
        future = (now + datetime.timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        user.set_custom('vip', {'level': 'pro', 'expire_time': future})
        user.save()

        self.assertTrue(user.is_vip())
        self.assertEqual(user.vip_level(), 'pro')

        # VIP 用户订阅上限为 15
        many_media = [f'Media_{i}' for i in range(20)]
        saved_vip = user.set_subscribed_media(many_media)
        self.assertEqual(len(saved_vip), 15)

        user.delete_instance()

    def test_guest_reader_access(self):
        # 访客无需登录即可访问 /reader，且全部开放阅读权限，无锁定内容
        resp = self.client.get('/reader')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Reader', resp.text)
        self.assertIn('g_isGuest = 1', resp.text)
        self.assertNotIn('"is_locked": true', resp.text)

        # 验证目录穿越拦截安全性
        resp_traversal = self.client.get('/reader/article/../../etc/passwd')
        self.assertEqual(resp_traversal.status_code, 403)

    def test_vip_coupon_generation_and_redemption(self):
        # 模拟管理员登录批量生成卡密
        with self.client.session_transaction() as sess:
            sess['login'] = 1
            sess['userName'] = 'admin'
            sess['role'] = 'admin'

        gen_resp = self.client.post('/admin/coupons/generate', data={'count': '3', 'days': '30'})
        self.assertEqual(gen_resp.status_code, 200)

        coupons_json = AppInfo.get_value(AppInfo.vipCoupons, '{}')
        coupons = json.loads(coupons_json)
        self.assertGreaterEqual(len(coupons), 3)

        sample_code = next(c for c, data in coupons.items() if not data.get('used'))
        self.assertTrue(sample_code.startswith('KE-M30-'))

        # 创建测试普通用户并登录兑换
        test_user = KeUser.get_or_none(KeUser.name == 'test_redeem_user')
        if test_user:
            test_user.delete_instance()
        test_user = KeUser(name='test_redeem_user')
        test_user.passwd_hash = test_user.hash_text('pwd123456')
        test_user.save()

        # 模拟该用户登录
        with self.client.session_transaction() as sess:
            sess['login'] = 1
            sess['userName'] = 'test_redeem_user'
            sess['role'] = 'user'

        redeem_resp = self.client.post('/vip/redeem', data={'code': sample_code})
        self.assertEqual(redeem_resp.status_code, 200)
        self.assertEqual(redeem_resp.json.get('status'), 'ok')

        # 刷新数据库验证 VIP 状态
        updated_user = KeUser.get_by_id(test_user.id)
        self.assertTrue(updated_user.is_vip())
        self.assertEqual(updated_user.vip_level(), 'pro')

        # 重复兑换同一卡密应被拒绝
        dup_resp = self.client.post('/vip/redeem', data={'code': sample_code})
        self.assertEqual(dup_resp.status_code, 200)
        self.assertNotEqual(dup_resp.json.get('status'), 'ok')

        updated_user.delete_instance()

    def test_reader_user_isolation_and_auth(self):
        # 1. 测试 ReaderUser 专享注册
        clean_reader = ReaderUser.get_or_none(ReaderUser.name == 'test_reader_isolated')
        if clean_reader:
            clean_reader.delete_instance()

        keuser_count_before = KeUser.select().count()

        signup_resp = self.client.post('/reader/signup', data={
            'username': 'test_reader_isolated',
            'password': 'reader_password_123',
            'confirm_password': 'reader_password_123',
            'next': '/reader'
        }, follow_redirects=False)

        self.assertEqual(signup_resp.status_code, 302)
        self.assertIn('/reader', signup_resp.headers.get('Location', ''))

        # 验证物理隔离：KeUser 表数量完全不变，新记录在 ReaderUser 表中
        self.assertEqual(KeUser.select().count(), keuser_count_before)
        r_user = ReaderUser.get_or_none(ReaderUser.name == 'test_reader_isolated')
        self.assertIsNotNone(r_user)
        self.assertTrue(r_user.verify_password('reader_password_123'))
        self.assertFalse(r_user.is_vip())
        self.assertEqual(r_user.vip_level(), 'free')

        # 2. 验证权限阻断：ReaderUser 无法访问系统后台 /my，被拦截跳转至 /login
        my_resp = self.client.get('/my')
        self.assertEqual(my_resp.status_code, 302)
        self.assertIn('/login', my_resp.headers.get('Location', ''))

        # 3. 验证 ReaderUser 访问 /reader 与 /vip
        reader_resp = self.client.get('/reader')
        self.assertEqual(reader_resp.status_code, 200)
        self.assertIn('g_isGuest = 0', reader_resp.text)
        self.assertIn('g_isVip = 0', reader_resp.text)

        vip_resp = self.client.get('/vip')
        self.assertEqual(vip_resp.status_code, 200)

        # 4. 验证 ReaderUser 兑换卡密并升级 VIP
        with self.client.session_transaction() as sess:
            sess['login'] = 1
            sess['userName'] = 'admin'
            sess['role'] = 'admin'
        gen_resp = self.client.post('/admin/coupons/generate', data={'count': '1', 'days': '60'})
        self.assertEqual(gen_resp.status_code, 200)
        coupons = json.loads(AppInfo.get_value(AppInfo.vipCoupons, '{}'))
        coupon_code = next(c for c, data in coupons.items() if not data.get('used'))

        # 切换回 ReaderUser 会话
        with self.client.session_transaction() as sess:
            sess.pop('login', None)
            sess.pop('userName', None)
            sess.pop('role', None)
            sess['reader_login'] = 1
            sess['reader_username'] = 'test_reader_isolated'

        redeem_resp = self.client.post('/vip/redeem', data={'code': coupon_code})
        self.assertEqual(redeem_resp.status_code, 200)
        self.assertEqual(redeem_resp.json.get('status'), 'ok')

        # 刷新 ReaderUser 验证 VIP 状态
        r_user_updated = ReaderUser.get_by_id(r_user.id)
        self.assertTrue(r_user_updated.is_vip())
        self.assertEqual(r_user_updated.vip_level(), 'pro')

        # 5. 验证媒体配额升级 (VIP 可订阅至 15 个媒体)
        fifteen_media = [f'Curated_{i}' for i in range(18)]
        saved = r_user_updated.set_subscribed_media(fifteen_media)
        self.assertEqual(len(saved), 15)

        # 6. 测试 ReaderLogout
        logout_resp = self.client.get('/reader/logout')
        self.assertEqual(logout_resp.status_code, 302)
        self.assertIn('/reader', logout_resp.headers.get('Location', ''))

        # 登出后恢复为访客
        guest_resp = self.client.get('/reader')
        self.assertIn('g_isGuest = 1', guest_resp.text)

        r_user_updated.delete_instance()

