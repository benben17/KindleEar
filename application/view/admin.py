#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#账号管理页面
#Author: cdhigh <https://github.com/cdhigh>
import datetime, json, uuid
from operator import attrgetter
from flask import Blueprint, request, url_for, render_template, redirect, current_app as app
from flask_babel import gettext as _
from ..base_handler import *
from ..back_end.db_models import *
from ..ke_utils import str_to_int, utcnow
from .login import CreateAccountIfNotExist

bpAdmin = Blueprint('bpAdmin', __name__)

def get_coupons_stats():
    try:
        coupons = json.loads(AppInfo.get_value(AppInfo.vipCoupons, '{}'))
    except Exception:
        coupons = {}
    total = len(coupons)
    used = sum(1 for c in coupons.values() if c.get('used'))
    unused = total - used
    return {'total': total, 'used': used, 'unused': unused}

# 账户管理页面
@bpAdmin.route("/admin", endpoint='Admin')
@login_required()
def Admin(user: KeUser):
    #只有管理员才能管理其他用户
    adminName = app.config['ADMIN_NAME']
    if user.name == adminName:
        users = sorted(KeUser.get_all(), key=attrgetter('created_time'))
        mailSrv = AppInfo.get_value(AppInfo.newUserMailService, 'admin')
        signupType = AppInfo.get_value(AppInfo.signupType, 'oneTimeCode')
        inviteCodes = AppInfo.get_value(AppInfo.inviteCodes, '')
        return render_template('admin.html', title='Account', tab='admin', users=users, adminName=adminName,
            mailSrv=mailSrv, signupType=signupType, inviteCodes=inviteCodes, tips='',
            couponsStats=get_coupons_stats(), generatedCoupons='')
    else:
        return render_template('change_password.html', tips='', tab='admin', user=user, shareKey=user.share_links.get('key'))

@bpAdmin.post("/admin", endpoint='AdminPost')
@login_required()
def AdminPost(user: KeUser):
    #只有管理员才能管理其他用户
    adminName = app.config['ADMIN_NAME']
    if user.name != adminName:
        return redirect(url_for('bpAdmin.Admin'))

    mailSrv = request.form.get('sm_service')
    signupType = request.form.get('signup_type')
    inviteCodes = request.form.get('invite_codes', '')
    AppInfo.set_value(AppInfo.newUserMailService, mailSrv)
    AppInfo.set_value(AppInfo.signupType, signupType)
    AppInfo.set_value(AppInfo.inviteCodes, inviteCodes)
    users = sorted(KeUser.get_all(), key=attrgetter('created_time'))
    return render_template('admin.html', title='Account', tab='admin', users=users, adminName=adminName,
            mailSrv=mailSrv, signupType=signupType, inviteCodes=inviteCodes, tips=_("Settings Saved!"),
            couponsStats=get_coupons_stats(), generatedCoupons='')

# 批量生成 VIP 卡密
@bpAdmin.post("/admin/coupons/generate", endpoint='AdminCouponsGeneratePost')
@login_required()
def AdminCouponsGeneratePost(user: KeUser):
    adminName = app.config['ADMIN_NAME']
    if user.name != adminName:
        return redirect(url_for('bpAdmin.Admin'))

    count = str_to_int(request.form.get('count', '10'), 10)
    days = str_to_int(request.form.get('days', '30'), 30)
    count = max(1, min(count, 200))

    try:
        coupons = json.loads(AppInfo.get_value(AppInfo.vipCoupons, '{}'))
    except Exception:
        coupons = {}

    prefix = f'KE-M{days}'
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new_codes = []
    for idx in range(count):
        rand_str = uuid.uuid4().hex[:8].upper()
        code = f'{prefix}-{rand_str[:4]}-{rand_str[4:]}'
        while code in coupons:
            rand_str = uuid.uuid4().hex[:8].upper()
            code = f'{prefix}-{rand_str[:4]}-{rand_str[4:]}'
        coupons[code] = {
            'days': days,
            'created_at': now_str,
            'used': False,
            'used_by': '',
            'used_at': ''
        }
        new_codes.append(code)

    AppInfo.set_value(AppInfo.vipCoupons, json.dumps(coupons))

    users = sorted(KeUser.get_all(), key=attrgetter('created_time'))
    mailSrv = AppInfo.get_value(AppInfo.newUserMailService, 'admin')
    signupType = AppInfo.get_value(AppInfo.signupType, 'oneTimeCode')
    inviteCodes = AppInfo.get_value(AppInfo.inviteCodes, '')
    generated_text = '\n'.join(new_codes)
    tips = _("Successfully generated {} VIP coupons!").format(count)

    return render_template('admin.html', title='Account', tab='admin', users=users, adminName=adminName,
        mailSrv=mailSrv, signupType=signupType, inviteCodes=inviteCodes, tips=tips,
        couponsStats=get_coupons_stats(), generatedCoupons=generated_text)

# 快速修改用户 VIP 权限
@bpAdmin.post("/admin/user_vip", endpoint='AdminUserVipPost')
@login_required(forAjax=True)
def AdminUserVipPost(user: KeUser):
    adminName = app.config['ADMIN_NAME']
    if user.name != adminName:
        return {'status': _("You do not have sufficient privileges.")}

    target_name = request.form.get('username', '').strip()
    action = request.form.get('action', '').strip()
    target_user = KeUser.get_or_none(KeUser.name == target_name)
    if not target_user:
        return {'status': _("User not found.")}

    vip_data = target_user.custom.get('vip', {})
    now = datetime.datetime.now()

    if action == 'add30':
        base_dt = now
        cur_expire_str = vip_data.get('expire_time')
        if cur_expire_str:
            try:
                cur_dt = datetime.datetime.strptime(cur_expire_str, '%Y-%m-%d %H:%M:%S')
                if cur_dt > now:
                    base_dt = cur_dt
            except Exception:
                pass
        new_expire = (base_dt + datetime.timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        vip_data['level'] = 'pro'
        vip_data['expire_time'] = new_expire
        target_user.set_custom('vip', vip_data)
        target_user.save()
        return {'status': 'ok', 'level': 'pro', 'expire_time': new_expire}
    elif action == 'cancel':
        vip_data['level'] = 'free'
        vip_data['expire_time'] = ''
        target_user.set_custom('vip', vip_data)
        target_user.save()
        return {'status': 'ok', 'level': 'free', 'expire_time': ''}
    else:
        return {'status': 'Invalid action'}

#管理员添加一个账号
@bpAdmin.route("/account/add", endpoint='AdminAddAccount')
@login_required()
def AdminAddAccount(user: KeUser):
    if user.name != app.config['ADMIN_NAME']:
        return redirect(url_for("bpLogin.Login"))
    else:
        return render_template('user_account.html', tips='', formTitle=_('Add account'), submitTitle=_('Add'), tab='admin')

@bpAdmin.post("/account/add", endpoint='AdminAddAccountPost')
@login_required()
def AdminAddAccountPost(user: KeUser):
    if user.name != app.config['ADMIN_NAME']:
        tips = _("You do not have sufficient privileges.")
        return render_template('user_account.html', tips=tips, formTitle=_('Add account'), submitTitle=_('Add'), tab='admin')
    
    form = request.form
    username = form.get('username', '')
    password1 = form.get('password1', '')
    password2 = form.get('password2', '')
    email = form.get('email', '')
    sm_service = form.get('sm_service')
    expiration = str_to_int(form.get('expiration', '0'))

    specialChars = ['<', '>', '&', '\\', '/', '%', '*', '.', '{', '}', ',', ';', '|', ' ']
    tips = ''
    if not all([username, password1, password2, email, sm_service]):
        tips = _("Some parameters are missing or wrong.")
    elif any([char in username for char in specialChars]):
        tips = _("The username includes unsafe chars.")
    elif password1 != password2:
        tips = _("The two new passwords are dismatch.")
    elif KeUser.get_or_none(KeUser.name == username):
        tips = _("Already exist the username.")
    else:
        sm_service = {'service': 'admin'} if sm_service == 'admin' else {}
        sender = user.cfg('email') if sm_service else email #和管理员一致则邮件发件地址也一致
        if not CreateAccountIfNotExist(username, password1, email, sender, sm_service, expiration):
            tips = _("The password includes non-ascii chars.")

    if tips:
        return render_template('user_account.html', tips=tips, formTitle=_('Add account'), submitTitle=_('Add'), 
            user=None, tab='admin')
    else:
        return redirect(url_for('bpAdmin.Admin'))

#直接AJAX删除一个账号
@bpAdmin.post("/account/delete", endpoint='AdminDeleteAccountAjax')
@login_required(forAjax=True)
def AdminDeleteAccountAjax(user: KeUser):
    adminName = app.config['ADMIN_NAME']
    name = request.form.get('name', '')
    if (user.name != adminName) or not name or (name == adminName):
        return {'status': _("You do not have sufficient privileges.")}
    
    dbItem = KeUser.get_or_none(KeUser.name == name)
    if not dbItem:
        return {'status': _("The username '{}' does not exist.").format(name)}
    else:
        dbItem.erase_traces() #删除账号订阅的书，白名单，过滤器等，完全的清理其痕迹
        dbItem.delete_instance()
        return {'status': 'ok'}

#修改自己的密码
@bpAdmin.route("/account/change", endpoint='AdminAccountChangeSelf')
@login_required()
def AdminAccountChangeSelf(user: KeUser):
    return redirect(url_for('bpAdmin.AdminAccountChange', name=user.name), code=307)

#修改密码，可能是修改自己的密码或管理员修改其他用户的密码
@bpAdmin.route("/account/change/<name>", endpoint='AdminAccountChange')
@login_required()
def AdminAccountChange(name: str, user: KeUser):
    tips = _('The password will not be changed if the fields are empties.')
    if user.name == name: #修改自己的密码和一些设置
        return render_template('change_password.html', tips=tips, tab='admin', user=user, shareKey=user.share_links.get('key'))
    elif user.name == app.config['ADMIN_NAME']: #管理员修改其他人的密码和其他设置
        dbItem = KeUser.get_or_none(KeUser.name == name)
        if dbItem:
            return render_template('user_account.html', tips=tips, formTitle=_('Edit account'), 
                submitTitle=_('Change'), user=dbItem, tab='admin')
        else:
            tips=_("The username '{}' does not exist.").format(name)
            return render_template('tipsback.html', title='error', urltoback=url_for('bpAdmin.Admin'), tips=tips)
    else:
        tips=_('You do not have sufficient privileges.')
        return render_template('tipsback.html', title='privileges', urltoback=url_for('bpAdmin.Admin'), tips=tips)

@bpAdmin.post("/account/change/<name>", endpoint='AdminAccountChangePost')
@login_required()
def AdminAccountChangePost(name: str, user: KeUser):
    form = request.form
    username = form.get('username')
    orgPwd = form.get('orgpwd', '')
    p1 = form.get('password1', '')
    p2 = form.get('password2', '')
    email = form.get('email')
    shareKey = form.get('shareKey')
    dbItem = None
    tips = ''
    if name != username:
        return render_template('tipsback.html', title='error', urltoback=url_for('bpAdmin.Admin'), 
            tips=_('Some parameters are missing or wrong.'))
    elif user.name == name: #修改自己的密码
        tips = ChangePassword(user, orgPwd, p1, p2, email, shareKey)
        return render_template('change_password.html', tips=tips, tab='admin', user=user, shareKey=user.share_links.get('key'))
    elif user.name == app.config['ADMIN_NAME']: #管理员修改其他账号
        email = form.get('email', '')
        smType = form.get('sm_service')
        expiration = str_to_int(form.get('expiration', '0'))

        dbItem = KeUser.get_or_none(KeUser.name == username)
        if not dbItem:
            tips = _("The username '{}' does not exist.").format(username)
        elif (p1 or p2) and (p1 != p2):
            tips = _("The two new passwords are dismatch.")
        else:
            if p1 and p2: #只有提供了两个密码才修改数据库中保存的密码
                dbItem.passwd_hash = dbItem.hash_text(p1)
            
            dbItem.expiration_days = expiration
            if expiration:
                dbItem.expires = utcnow() + datetime.timedelta(days=expiration)
            else:
                dbItem.expires = None
            if smType == 'admin':
                dbItem.send_mail_service = {'service': 'admin'}
            elif dbItem.send_mail_service.get('service') == 'admin': #从和管理员一致变更为独立设置
                dbItem.send_mail_service = {}
            dbItem.set_cfg('email', email)
            dbItem.save()
            tips = _("Change success.")
    
    return render_template('user_account.html', tips=tips, formTitle=_('Edit account'), 
        submitTitle=_('Change'), user=dbItem, tab='admin')

#修改一个账号的密码，返回执行结果字符串
def ChangePassword(user, orgPwd, p1, p2, email, shareKey):
    if not email or not shareKey:
        tips = _("Some parameters are missing or wrong.")
    elif p1 != p2:
        tips = _("The two new passwords are dismatch.")
    elif any((orgPwd, p1, p2)) and not user.verify_password(orgPwd):
        #如果不修改密码，则三个密码都必须为空，有任何一个不为空，都表示要修改密码
        tips = _("The old password is wrong.")
    else:
        tips = _("Changes saved successfully.")

        if any((orgPwd, p1, p2)):
            user.passwd_hash = user.hash_text(p1)
        user.set_cfg('email', email)
        shareLinks = user.share_links
        shareLinks['key'] = shareKey
        user.share_links = shareLinks
        if user.name == app.config['ADMIN_NAME']: #如果管理员修改email，也同步更新其他用户的发件地址
            user.set_cfg('sender', email)
            SyncSenderAddress(user)
        else: #其他人修改自己的email，根据设置确定是否要同步到发件地址
            sm_service = user.send_mail_service
            if not sm_service or sm_service.get('service', 'admin') != 'admin':
                user.set_cfg('sender', email)
                    
        user.save()
    return tips

#将管理员的email同步到所有用户
def SyncSenderAddress(adminUser):
    for user in list(KeUser.get_all(KeUser.name != app.config['ADMIN_NAME'])):
        sm_service = user.send_mail_service
        if sm_service and sm_service.get('service', 'admin') == 'admin':
            user.set_cfg('sender', adminUser.cfg('email'))
            user.save()
