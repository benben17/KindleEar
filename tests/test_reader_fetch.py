#!/usr/bin/env python3
# -*- coding:utf-8 -*-
import os
from test_base import *
from application.back_end.db_models import *

class ReaderFetchTestCase(BaseTestCase):
    login_required = 'admin'

    def test_reader_fetch_missing_id(self):
        resp = self.client.post('/reader/fetch', data={'id': ''})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('status', resp.json)
        self.assertNotEqual(resp.json['status'], 'ok')

    def test_reader_fetch_nonexistent_recipe(self):
        resp = self.client.post('/reader/fetch', data={'id': 'custom:99999'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('status', resp.json)
        self.assertIn('not found', resp.json['status'].lower())

    def test_reader_route_with_book_param(self):
        resp = self.client.get('/reader?book=2026-09-14/001_NewYorkTimes')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Reader', resp.text)
