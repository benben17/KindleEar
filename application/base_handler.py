#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#主要针对网页显示之类的一些公共支持工具函数
#Author: cdhigh <https://github.com/cdhigh>
from typing import Union
from functools import wraps
from flask import request, redirect, session, url_for
from .back_end.db_models import *

#一些共同的工具函数，工具函数都是小写+下划线形式

#确认登录的装饰器，使用此装饰器的函数都需要有一个参数 user
#forAjax:是否返回一个json字典
def login_required(forAjax=False):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_url = request.url
            user = get_login_user()
            if user:
                kwargs['user'] = user
                return func(*args, **kwargs)
            elif forAjax:
                return redirect(url_for("bpLogin.NeedLoginAjax"))
            else:
                return redirect(url_for("bpLogin.Login", next=current_url))
        return wrapper
    return decorator

#查询当前登录用户名，在使用此函数前最好保证已经登录
#返回一个数据库行实例，而不是一个字符串
def get_login_user() -> Union[KeUser,None]:
    name = session.get('userName', '') if (session.get('login', '') == 1) else ''
    return KeUser.get_or_none(KeUser.name == name) if name else None

#查询当前登录的阅读器读者用户 (ReaderUser)
#若系统管理员/用户 (KeUser) 已登录后台，亦具备无缝兼容访问权限
def get_reader_user() -> Union[ReaderUser, KeUser, None]:
    # 1. 优先检查专属的读者会话 session['reader_login']
    reader_name = session.get('reader_username', '') if (session.get('reader_login', '') == 1) else ''
    if reader_name:
        u = ReaderUser.get_or_none(ReaderUser.name == reader_name)
        if u and getattr(u, 'status', 1) == 1:
            return u
    # 2. 兼容系统用户已在后台登录的情况
    ke_user = get_login_user()
    if ke_user:
        return ke_user
    return None

#针对阅读器读者专属页面的登录鉴权装饰器
def reader_login_required(forAjax=False):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_url = request.url
            user = get_reader_user()
            if user:
                kwargs['user'] = user
                return func(*args, **kwargs)
            elif forAjax:
                return {'status': 'need_login', 'msg': 'Please log in first.'}
            else:
                return redirect(url_for("bpReaderAuth.ReaderLogin", next=current_url))
        return wrapper
    return decorator
    
#记录投递记录到数据库
def save_delivery_log(user: KeUser, book: str, size: int, status='ok', to: Union[str,list,None]=None):
    name = user.name
    to = to or user.cfg('kindle_email') #type: ignore
    if isinstance(to, list):
        to = ','.join(to)
    
    try:
        DeliverLog.create(user=name, to=to, size=size, time_str=user.local_time("%Y-%m-%d %H:%M"), 
            book=book, status=status)
    except Exception as e:
        default_log.warning('DeliverLog failed to save: {}'.format(e))
