#!/usr/bin/env python3
# -*- coding:utf-8 -*-
# KindleEar VIP 会员中心与卡密兑换模块
import json
import datetime
from flask import Blueprint, render_template, request, current_app as app
from flask_babel import gettext as _
from ..base_handler import login_required
from ..back_end.db_models import KeUser, AppInfo, BookedRecipe

bpVip = Blueprint('bpVip', __name__)

def get_curated_media_list():
    """动态获取管理员在 /my 页面已订阅的精选媒体清单"""
    adminName = app.config.get('ADMIN_NAME', 'admin')
    admin_user = KeUser.get_or_none(KeUser.name == adminName)
    if not admin_user:
        return []
    booked = admin_user.get_booked_recipe()
    media_list = []
    for b in booked:
        media_list.append({
            'title': b.title,
            'id': b.recipe_id,
            'desc': b.description or '',
        })
    return media_list

@bpVip.route("/vip", endpoint='VipCenter')
@login_required()
def VipCenter(user: KeUser):
    """会员中心主页"""
    is_vip = user.is_vip()
    vip_data = user.custom.get('vip', {})
    expire_time = vip_data.get('expire_time', '')
    subscribed_media = user.get_subscribed_media()
    max_media = 15 if is_vip else 2
    
    all_media = get_curated_media_list()
    # 如果用户尚未勾选且可选列表存在，前2个作为默认选中的展示
    if not subscribed_media and all_media:
        subscribed_media = [m['title'] for m in all_media[:2]]

    return render_template('vip.html', user=user, is_vip=is_vip, expire_time=expire_time,
        subscribed_media=subscribed_media, max_media=max_media, all_media=all_media,
        level=user.vip_level(), tab='vip')

@bpVip.post("/vip/redeem", endpoint='VipRedeemPost')
@login_required(forAjax=True)
def VipRedeemPost(user: KeUser):
    """卡密原子兑换接口"""
    code = request.form.get('code', '').strip().upper()
    if not code:
        return {'status': _("Please enter the coupon code.")}

    try:
        coupons = json.loads(AppInfo.get_value(AppInfo.vipCoupons, '{}'))
    except Exception:
        coupons = {}

    coupon = coupons.get(code)
    if not coupon or coupon.get('used'):
        return {'status': _("Invalid or already used coupon code.")}

    days = coupon.get('days', 30)
    now = datetime.datetime.now()

    # 原子标记卡密为已使用
    coupon['used'] = True
    coupon['used_by'] = user.name
    coupon['used_at'] = now.strftime('%Y-%m-%d %H:%M:%S')
    coupons[code] = coupon
    AppInfo.set_value(AppInfo.vipCoupons, json.dumps(coupons))

    # 累加或更新用户 VIP 有效期
    vip_data = user.custom.get('vip', {})
    cur_expire_str = vip_data.get('expire_time')
    base_dt = now
    if cur_expire_str:
        try:
            cur_dt = datetime.datetime.strptime(cur_expire_str, '%Y-%m-%d %H:%M:%S')
            if cur_dt > now:
                base_dt = cur_dt
        except Exception:
            pass

    new_expire = (base_dt + datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    vip_data['level'] = 'pro'
    vip_data['expire_time'] = new_expire
    user.set_custom('vip', vip_data)
    user.save()

    msg = _("VIP activated successfully! Valid until: {}").format(new_expire)
    return {'status': 'ok', 'msg': msg, 'expire_time': new_expire}

@bpVip.post("/vip/subscribe_media", endpoint='VipSubscribeMediaPost')
@login_required(forAjax=True)
def VipSubscribeMediaPost(user: KeUser):
    """用户更新已订媒体清单"""
    media_raw = request.form.get('media', '')
    if media_raw:
        media_list = [m.strip() for m in media_raw.split(',') if m.strip()]
    else:
        media_list = request.form.getlist('media[]')

    saved = user.set_subscribed_media(media_list)
    limit = 15 if user.is_vip() else 2
    return {'status': 'ok', 'media': saved, 'limit': limit}
