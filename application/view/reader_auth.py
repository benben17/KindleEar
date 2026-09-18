#!/usr/bin/env python3
# -*- coding:utf-8 -*-
# 在线阅读器专享读者账户认证模块 (与系统后台 KeUser 物理隔离)
import datetime
from urllib.parse import urljoin, urlparse
from flask import Blueprint, render_template, request, redirect, session, url_for, current_app as app
from flask_babel import gettext as _
from ..back_end.db_models import ReaderUser, KeUser
from ..ke_utils import utcnow

bpReaderAuth = Blueprint('bpReaderAuth', __name__)

specialChars = ['<', '>', '&', '\\', '/', '%', '*', '.', '{', '}', ',', ';', '|', ' ']

def is_safe_redirect_url(target: str) -> bool:
    if not target or target.strip().startswith("//") or ('\n' in target) or ('\r' in target):
        return False
    target = target.strip()
    host_url = getattr(request, "host_url", "") or getattr(request, "url_root", "")
    resolved = urljoin(host_url, target)
    ref = urlparse(host_url)
    test = urlparse(resolved)
    return (test.scheme in ("http", "https")) and (ref.netloc == test.netloc)

@bpReaderAuth.route("/reader/login", endpoint='ReaderLogin')
def ReaderLogin():
    next_url = request.args.get('next', '/reader')
    if session.get('reader_login') == 1 and session.get('reader_username'):
        return redirect(next_url if is_safe_redirect_url(next_url) else '/reader')
    tips = request.args.get('tips', '')
    return render_template('reader_login.html', mode='login', next=next_url, tips=tips)

@bpReaderAuth.post("/reader/login", endpoint='ReaderLoginPost')
def ReaderLoginPost():
    name = request.form.get('username', '').strip()
    passwd = request.form.get('password', '')
    next_url = request.form.get('next', '/reader')
    if not is_safe_redirect_url(next_url):
        next_url = '/reader'

    tips = ''
    if not name:
        tips = _("Username is empty.")
    elif len(name) > 30:
        tips = _("The len of username reached the limit of 30 chars.")
    elif any(char in name for char in specialChars):
        tips = _("The username includes unsafe chars.")

    if tips:
        return render_template('reader_login.html', mode='login', next=next_url, tips=tips)

    # 1. 优先验证 ReaderUser 表（读者账户）
    r_user = ReaderUser.get_or_none(ReaderUser.name == name)
    if r_user:
        if r_user.verify_password(passwd):
            if getattr(r_user, 'status', 1) != 1:
                return render_template('reader_login.html', mode='login', next=next_url, tips=_("Account is disabled."))
            session['reader_login'] = 1
            session['reader_username'] = r_user.name
            session.permanent = True
            r_user.last_login = datetime.datetime.now()
            r_user.save()
            return redirect(next_url)
        else:
            return render_template('reader_login.html', mode='login', next=next_url, tips=_("Password is wrong."))

    # 2. 兼容系统管理员账户 (KeUser) 使用管理账号直接登录阅读器
    ke_user = KeUser.get_or_none(KeUser.name == name)
    admin_name = app.config.get('ADMIN_NAME', 'admin')
    if ke_user and (name == admin_name or getattr(ke_user, 'role', '') == 'admin'):
        if ke_user.verify_password(passwd):
            session['reader_login'] = 1
            session['reader_username'] = ke_user.name
            session['login'] = 1
            session['userName'] = ke_user.name
            session.permanent = True
            return redirect(next_url)

    return render_template('reader_login.html', mode='login', next=next_url, tips=_("The username does not exist or password is wrong."))

@bpReaderAuth.route("/reader/signup", endpoint='ReaderSignup')
def ReaderSignup():
    next_url = request.args.get('next', '/reader')
    if session.get('reader_login') == 1 and session.get('reader_username'):
        return redirect(next_url if is_safe_redirect_url(next_url) else '/reader')
    tips = request.args.get('tips', '')
    return render_template('reader_login.html', mode='signup', next=next_url, tips=tips)

@bpReaderAuth.post("/reader/signup", endpoint='ReaderSignupPost')
def ReaderSignupPost():
    name = request.form.get('username', '').strip()
    passwd = request.form.get('password', '')
    confirm_passwd = request.form.get('confirm_password', '')
    next_url = request.form.get('next', '/reader')
    if not is_safe_redirect_url(next_url):
        next_url = '/reader'

    tips = ''
    if not name:
        tips = _("Username is empty.")
    elif len(name) < 3 or len(name) > 30:
        tips = _("Username must be between 3 and 30 characters.")
    elif any(char in name for char in specialChars):
        tips = _("The username includes unsafe chars.")
    elif not passwd:
        tips = _("Password is empty.")
    elif len(passwd) < 4:
        tips = _("Password must be at least 4 characters.")
    elif passwd != confirm_passwd:
        tips = _("The two new passwords do not match.")

    if tips:
        return render_template('reader_login.html', mode='signup', next=next_url, tips=tips)

    # 检查用户名是否已存在于 ReaderUser 表
    if ReaderUser.get_or_none(ReaderUser.name == name):
        return render_template('reader_login.html', mode='signup', next=next_url, tips=_("The username already exists."))

    # 密码哈希落盘
    pwd_hash = ReaderUser.create_password_hash(passwd)
    try:
        r_user = ReaderUser.create(
            name=name,
            passwd_hash=pwd_hash,
            vip_level_val='free',
            status=1,
            created_time=utcnow()
        )
        session['reader_login'] = 1
        session['reader_username'] = r_user.name
        session.permanent = True
        return redirect(next_url)
    except Exception as e:
        return render_template('reader_login.html', mode='signup', next=next_url, tips=f"{_('Registration failed')}: {str(e)}")

@bpReaderAuth.route("/reader/logout", endpoint='ReaderLogout')
def ReaderLogout():
    session.pop('reader_login', None)
    session.pop('reader_username', None)
    return redirect(url_for('bpReader.ReaderRoute'))
