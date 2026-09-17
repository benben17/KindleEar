#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#KindleEar在线RSS阅读器，为电子墨水屏进行了专门优化
#Author: cdhigh <https://github.com/cdhigh>
import os, json, shutil, time, re, html
from functools import wraps
from operator import itemgetter
from lxml import etree #type:ignore
from bs4 import BeautifulSoup
from flask import Blueprint, render_template, session, request, send_from_directory, make_response, current_app as app
from flask_babel import gettext as _
from build_ebook import html_to_book
from ..base_handler import *
from ..ke_utils import xml_escape, xml_unescape, str_to_int, str_to_float, str_to_bool
from ..back_end.db_models import *
from ..back_end.send_mail_adpt import send_to_kindle
from .settings import get_locale, LangMap
from .vip import get_curated_media_list

bpReader = Blueprint('bpReader', __name__)

#阅读器路由每个函数校验基本配置代码基本一致，使用此装饰器避免重复代码
#使用此装饰器的函数需要有形参 userDir
def reader_route_preprocess(forAjax=False):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            oebDir = app.config['EBOOK_SAVE_DIR']
            if not oebDir:
                msg = _("Online reading feature has not been activated yet.")
                return {'status': msg} if forAjax else msg

            user = kwargs.get('user') #login_required装饰器能保证user一定有效
            userDir = os.path.join(oebDir, user.name).replace('\\', '/') #type:ignore
            kwargs['userDir'] = userDir
            return func(*args, **kwargs)
        return wrapper
    return decorator

#在线阅读器首页
@bpReader.route("/reader", endpoint='ReaderRoute')
def ReaderRoute():
    userName = request.args.get('username')
    password = request.args.get('password')
    
    #为了方便在墨水屏上使用，如果没有登录的话，可以使用查询字符串传递用户名和密码
    if userName and password:
        user = KeUser.get_or_none(KeUser.name == userName)
        if user and user.verify_password(password):
            session['login'] = 1
            session['userName'] = userName
            session['role'] = 'admin' if userName == app.config['ADMIN_NAME'] else 'user'
        else:
            time.sleep(5) #防止暴力破解
            user = None
    else:
        user = get_login_user()

    is_guest = (user is None)
    is_vip = user.is_vip() if user else False
    vip_lvl = user.vip_level() if user else 'guest'

    oebDir = app.config.get('EBOOK_SAVE_DIR') or os.environ.get('EBOOK_SAVE_DIR')
    adminName = app.config.get('ADMIN_NAME', 'admin')

    # 从管理员在 /my 已订内容中动态获取精选媒体池
    curated_media = get_curated_media_list()
    curated_titles = [m['title'] for m in curated_media]

    if user:
        subscribed_media = user.get_subscribed_media()
        if not subscribed_media and curated_titles:
            subscribed_media = curated_titles if is_vip else curated_titles[:2]
    else:
        subscribed_media = curated_titles[:1] if curated_titles else []

    savedList = []
    comicTitle = 'Nothing here'
    if oebDir:
        adminDir = os.path.join(oebDir, adminName).replace('\\', '/')
        savedMap = {}
        # 1. 扫描管理员目录（集中式公共报刊池）
        if os.path.isdir(adminDir):
            for day in GetSavedOebList(adminDir):
                d = day['date']
                savedMap[d] = {b['title']: b for b in day['books']}

        # 2. 若普通用户有私有书籍，合并入书架
        if user and user.name != adminName:
            userDir = os.path.join(oebDir, user.name).replace('\\', '/')
            if os.path.isdir(userDir):
                for day in GetSavedOebList(userDir):
                    d = day['date']
                    if d not in savedMap:
                        savedMap[d] = {}
                    for b in day['books']:
                        if b['title'] not in savedMap[d]:
                            savedMap[d][b['title']] = b

        for d in sorted(savedMap.keys(), reverse=True):
            b_list = list(savedMap[d].values())
            b_list.sort(key=lambda x: x['title'])
            savedList.append({'date': d, 'books': b_list})
    else:
        comicTitle = 'Not activated'

    # 权限控制与标题全量透出投影
    targetBook = request.args.get('book', '').strip()
    initArticle = ''
    allowed_prefixes = []

    guest_reads = session.get('guest_read_articles', [])
    guest_reads_count = len(guest_reads)
    guest_unlocked_count = 0
    latest_date = savedList[0]['date'] if savedList else ''

    projectedBooks = []
    for day in savedList:
        date = day['date']
        is_latest_day = (date == latest_date)
        day_books = []

        for b in day['books']:
            b_title = b['title']
            is_subscribed = (b_title in subscribed_media) or (not subscribed_media)
            if is_vip and (getattr(user, 'role', '') == 'admin' or (user and user.name == adminName)):
                is_subscribed = True

            projected_articles = []
            for a_idx, art in enumerate(b['articles']):
                art_src = art['src']
                art_title = art['title']
                art_dir = os.path.dirname(art_src)

                is_locked = False
                if is_vip:
                    # VIP: 30天归档全量畅读
                    if is_subscribed:
                        is_locked = False
                        allowed_prefixes.append(art_dir)
                    else:
                        is_locked = True
                elif not is_guest:
                    # 注册普通用户：仅最新一天，最多2个已选媒体，前10篇
                    if is_latest_day and is_subscribed and a_idx < 10:
                        is_locked = False
                        allowed_prefixes.append(art_dir)
                    else:
                        is_locked = True
                else:
                    # 未登录访客：仅最新一天，全刊前5篇
                    if is_latest_day and guest_unlocked_count < 5 and guest_reads_count < 5:
                        is_locked = False
                        allowed_prefixes.append(art_dir)
                        guest_unlocked_count += 1
                    else:
                        is_locked = True

                projected_articles.append({
                    'title': art_title,
                    'src': art_src if not is_locked else '',
                    'real_src': art_src,
                    'is_locked': is_locked,
                })

            day_books.append({
                'title': b_title,
                'language': b.get('language', 'en'),
                'bookDir': b['bookDir'],
                'articles': projected_articles,
                'is_subscribed': is_subscribed,
            })

        projectedBooks.append({'date': date, 'books': day_books})

    session['allowed_article_prefixes'] = list(set(allowed_prefixes))
    session.modified = True

    # 寻找首篇可阅读文章
    if targetBook:
        for day in projectedBooks:
            for b in day.get('books', []):
                if (b.get('bookDir') == targetBook) or (targetBook in b.get('bookDir', '')):
                    for art in b.get('articles', []):
                        if not art['is_locked'] and art['src']:
                            initArticle = url_for('bpReader.ReaderArticleRoute', path=art['src'])
                            break
                if initArticle:
                    break
            if initArticle:
                break

    if not initArticle:
        for day in projectedBooks:
            for b in day.get('books', []):
                for art in b.get('articles', []):
                    if not art['is_locked'] and art['src']:
                        initArticle = url_for('bpReader.ReaderArticleRoute', path=art['src'])
                        break
                if initArticle:
                    break
            if initArticle:
                break

    if not initArticle:
        initArticle = url_for('bpReader.ReaderArticleNoFoundRoute', tips='')

    params = (user.cfg('reader_params') if user else session.get('reader_params')) or {'fontSize': 1.0, 'allowLinks': 1, 'topleftDict': 1, 'darkMode': 0, 'inkMode': 0}
    shareKey = user.share_links.get('key') if user else ''
    docLang = 'Chinese' if get_locale().startswith('zh') else 'English'
    helpPage = f'https://cdhigh.github.io/KindleEar/{docLang}/reader.html'

    return render_template('reader.html',
        oebBooks=json.dumps(projectedBooks, ensure_ascii=False),
        initArticle=initArticle,
        params=params,
        shareKey=shareKey,
        comicTitle=comicTitle,
        helpPage=helpPage,
        isZh=(1 if docLang == 'Chinese' else 0),
        user=user,
        isGuest=(1 if is_guest else 0),
        isVip=(1 if is_vip else 0),
        vipLevel=vip_lvl,
        subscribedMedia=json.dumps(subscribed_media, ensure_ascii=False),
        allCuratedMedia=json.dumps(curated_media, ensure_ascii=False)
    )

#在线阅读器的404页面
@bpReader.route("/reader/404", endpoint='ReaderArticleNoFoundRoute')
def ReaderArticleNoFoundRoute():
    tips = request.args.get('tips')
    oebDir = app.config.get('EBOOK_SAVE_DIR') or os.environ.get('EBOOK_SAVE_DIR')
    if not oebDir:
        tips = _("Online reading feature has not been activated yet.")
    elif tips is None:
        tips = _('The article is missing?')
    user = get_login_user()
    params = user.cfg('reader_params') if user else {}
    return render_template('reader_404.html', tips=tips.strip(), params=params)

#获取文章或图像内容（带安全网关鉴权）
@bpReader.route("/reader/article/<path:path>", endpoint='ReaderArticleRoute')
def ReaderArticleRoute(path: str):
    if '..' in path:
        return ("Forbidden", 403)

    oebDir = app.config.get('EBOOK_SAVE_DIR') or os.environ.get('EBOOK_SAVE_DIR')
    if not oebDir or not os.path.isdir(oebDir):
        return render_template('reader_404.html', tips=_("Online reading feature has not been activated yet."), params={})

    user = get_login_user()
    is_vip = user.is_vip() if user else False
    is_guest = (user is None)

    is_html = path.endswith(('.html', '.htm'))
    allowed_prefixes = session.get('allowed_article_prefixes', [])
    in_whitelist = any(path.startswith(p) for p in allowed_prefixes)

    if not is_vip:
        if is_guest:
            guest_reads = session.get('guest_read_articles', [])
            if is_html:
                if path not in guest_reads:
                    if len(guest_reads) >= 5 or not in_whitelist:
                        return render_article_lock_page(is_guest=True)
                    guest_reads.append(path)
                    session['guest_read_articles'] = guest_reads
                    session.modified = True
            else:
                if not in_whitelist:
                    return ("Forbidden", 403)
        else: # Free user
            if not in_whitelist:
                if is_html:
                    return render_article_lock_page(is_guest=False)
                else:
                    return ("Forbidden", 403)

    adminName = app.config.get('ADMIN_NAME', 'admin')
    adminDir = os.path.join(oebDir, adminName).replace('\\', '/')
    userDir = os.path.join(oebDir, user.name).replace('\\', '/') if user else ''

    targetDir = ''
    if os.path.isfile(os.path.join(adminDir, path)):
        targetDir = adminDir
    elif userDir and os.path.isfile(os.path.join(userDir, path)):
        targetDir = userDir

    if not targetDir:
        return render_template('reader_404.html', tips=_('The article is missing?'), params=user.cfg('reader_params') if user else {})

    resp = send_from_directory(targetDir, path)
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

#推送一篇文章（仅限 VIP 会员，不支持整本书推送）
@bpReader.post("/reader/push", endpoint='ReaderPushPost')
@login_required(forAjax=True)
def ReaderPushPost(user: KeUser):
    type_ = request.form.get('type')
    src = request.form.get('src', '') #2024-05-30/KindleEar/feed_0/article_1/index.html
    title = request.form.get('title', '')
    language = request.form.get('language', '')

    if type_ == 'book':
        return {'status': _("Book download and export are disabled. Please read online.")}

    if not user.is_vip():
        return {'status': _("Pushing articles to Kindle is a VIP exclusive privilege. Please upgrade to VIP.")}

    if not ((type_ in ('book', 'article')) and ('/' in src) and title):
        return {'status': _("Some parameters are missing or wrong.")}

    title = xml_unescape(title)
    oebDir = app.config.get('EBOOK_SAVE_DIR') or os.environ.get('EBOOK_SAVE_DIR')
    adminName = app.config.get('ADMIN_NAME', 'admin')
    adminDir = os.path.join(oebDir, adminName).replace('\\', '/') if oebDir else ''
    userDir = os.path.join(oebDir, user.name).replace('\\', '/') if oebDir else ''

    targetDir = userDir if (userDir and os.path.isfile(os.path.join(userDir, src))) else adminDir
    msg = PushSingleArticle(src, title, user, targetDir, language)
    return {'status': msg}

#更新用户订阅的媒体清单
@bpReader.post("/reader/subscribe_media", endpoint='ReaderSubscribeMediaPost')
def ReaderSubscribeMediaPost():
    user = get_login_user()
    if not user:
        return {'status': _("Please log in first to save your media subscriptions.")}

    media_raw = request.form.get('media', '')
    if media_raw:
        media_list = [m.strip() for m in media_raw.split(',') if m.strip()]
    else:
        media_list = request.form.getlist('media[]')

    saved = user.set_subscribed_media(media_list)
    limit = 15 if user.is_vip() else 2
    return {'status': 'ok', 'media': saved, 'limit': limit}

#删除某些书籍
@bpReader.post("/reader/delete", endpoint='ReaderDeletePost')
@login_required(forAjax=True)
def ReaderDeletePost(user: KeUser):
    books = request.form.get('books', '')
    if not books:
        return {'status': _("Some parameters are missing or wrong.")}

    oebDir = app.config.get('EBOOK_SAVE_DIR') or os.environ.get('EBOOK_SAVE_DIR')
    if not oebDir:
        return {'status': _("Online reading feature has not been activated yet.")}

    adminName = app.config.get('ADMIN_NAME', 'admin')
    userDir = os.path.join(oebDir, user.name).replace('\\', '/')
    adminDir = os.path.join(oebDir, adminName).replace('\\', '/')

    for book in books.split('|'):
        if '..' in book: #防范文件系统路径攻击
            continue
        targetBase = adminDir if (user.name == adminName or getattr(user, 'role', '') == 'admin') else userDir
        bkDir = os.path.join(targetBase, book)
        dateDir = os.path.dirname(bkDir)
        if os.path.isdir(bkDir):
            try:
                shutil.rmtree(bkDir)
            except Exception as e:
                default_log.warning(f'Failed to delete dir: {bkDir}: {e}')

            #如果目录为空，则将目录也一并删除
            if os.path.isdir(dateDir) and not os.listdir(dateDir):
                try:
                    shutil.rmtree(dateDir)
                except:
                    pass
    return {'status': 'ok'}

#即刻抓取单个Recipe并保存到书架供在线阅读
@bpReader.post("/reader/fetch", endpoint='ReaderFetchPost')
@login_required(forAjax=True)
def ReaderFetchPost(user: KeUser):
    recipeId = request.form.get('id', '').strip()
    if not recipeId:
        return {'status': _("Some parameters are missing or wrong.")}

    oebDir = app.config.get('EBOOK_SAVE_DIR') or os.environ.get('EBOOK_SAVE_DIR')
    if not oebDir or not os.path.isdir(oebDir):
        return {'status': _("Online reading feature has not been activated yet.")}

    recipeType, dbId = Recipe.type_and_id(recipeId)
    if recipeType != 'builtin':
        rec = Recipe.get_by_id_or_none(dbId)
        if not rec or (rec.user != user.name and user.role != 'admin'):
            return {'status': f"Recipe not found: {recipeId}"}

    from ..work.worker import GetAllRecipeSrc
    from calibre.web.feeds.recipes import compile_recipe
    from build_ebook import convert_book
    from urlopener import UrlOpener

    UrlOpener.set_proxy(user.cfg('proxy'))
    srcDict = GetAllRecipeSrc(user, [recipeId])
    if not srcDict:
        return {'status': f"Recipe not found: {recipeId}"}

    title, (bked, recipeDb, src) = next(iter(srcDict.items()))
    try:
        rc = compile_recipe(src)
    except Exception as e:
        default_log.warning(f"Failed to compile recipe {title}: {e}")
        return {'status': f"Failed to compile recipe: {e}"}

    if not rc:
        return {'status': _("Failed to compile recipe.")}

    if rc.language in (None, '', 'und'):
        rc.language = user.book_cfg('language')
    elif rc.language:
        rc.language = rc.language.replace('_', '-').lower()

    rc.delivery_reason = 'manual'
    userCss = user.get_extra_css()
    rc.extra_css = f"{rc.extra_css}\n\n{userCss}" if rc.extra_css else userCss

    if bked:
        rc.translator = bked.translator.copy() if isinstance(bked.translator, dict) else {}
        rc.tts = {}
        rc.summarizer = bked.summarizer.copy() if isinstance(bked.summarizer, dict) else {}
        if rc.needs_subscription:
            rc.username = bked.account
            rc.password = bked.password
    else:
        rc.translator = {}
        rc.tts = {}
        rc.summarizer = {}


    options = {'force_save_webshelf': True}
    try:
        book = convert_book(rc, 'recipe', user, options=options)
    except Exception as e:
        default_log.warning(f"convert_book failed for {title}: {e}")
        return {'status': f"Build ebook failed: {e}"}

    savedBookDir = options.get('saved_book_dir')
    if not savedBookDir or not book:
        return {'status': 'nonews', 'msg': _("No new articles found in this feed.")}

    return {
        'status': 'ok',
        'book': savedBookDir,
        'title': title,
        'url': url_for('bpReader.ReaderRoute', book=savedBookDir)
    }

#设置阅读器的默认参数
@bpReader.post("/reader/settings", endpoint='ReaderSettingsPost')
def ReaderSettingsPost():
    user = get_login_user()
    form = request.form
    fontSize = str_to_float(form.get('fontSize', '1.0'), 1.0)
    allowLinks = 1 if str_to_bool(form.get('allowLinks', 'true')) else 0
    topleftDict = 1 if str_to_bool(form.get('topleftDict', 'true')) else 0
    darkMode = 1 if str_to_bool(form.get('darkMode', 'true')) else 0
    inkMode = 1 if str_to_bool(form.get('inkMode', 'true')) else 0
    if user:
        params = user.cfg('reader_params')
        params.update({'fontSize': fontSize, 'allowLinks': allowLinks, 'inkMode': inkMode, 
            'topleftDict': topleftDict, 'darkMode': darkMode})
        user.set_cfg('reader_params', params)
        user.save()
    else:
        params = session.get('reader_params', {})
        params.update({'fontSize': fontSize, 'allowLinks': allowLinks, 'inkMode': inkMode, 
            'topleftDict': topleftDict, 'darkMode': darkMode})
        session['reader_params'] = params
        session.modified = True
    return {'status': 'ok'}

#网页查词
@bpReader.route("/reader/dict", endpoint='ReaderDictRoute')
def ReaderDictRoute():
    user = get_login_user()
    from dictionary import all_dict_engines

    #刷新词典列表，方便在不重启服务的情况下添加删除离线词典文件
    for dic in all_dict_engines.values():
        if hasattr(dic, 'refresh'):
            dic.refresh()
    
    engines = {name: {'databases': klass.databases, 'mode': klass.mode} for name,klass in all_dict_engines.items()}
    return render_template('word_lookup.html', user=user, engines=engines, tips='', langMap=LangMap())

#Api查词
@bpReader.post("/reader/dict", endpoint='ReaderDictPost')
def ReaderDictPost():
    user = get_login_user()
    from dictionary import CreateDictInst, GetDictDisplayName
    form = request.form
    word = form.get('word', '').strip()
    language = form.get('language', '').replace('_', '-').split('-')[0].lower() #书本语种
    if not word:
        return {'status': _("The text is empty.")}

    #为一个字典列表[{language:,engine:,database:,}]
    dictParams = user.cfg('reader_params').get('dicts', []) if user else []

    #优先使用网页传递过来的引擎和数据库参数
    engine = form.get('engine')
    database = form.get('database')
    if not engine or not database:
        defDict = {}
        params = {}
        for item in dictParams:
            itemLang = item.get('language', 'und')
            if not itemLang or (itemLang == 'und'):
                defDict = item
            elif not params and (itemLang == language):
                params = item
        if not params:
            params = defDict
        engine = params.get('engine', '')
        database = params.get('database', '')
    
    inst = CreateDictInst(engine, database)
    #将其他可选的词典信息也传递给网页
    others = []
    added = set()
    for e in dictParams:
        itemEngine = e.get('engine')
        itemDb = e.get('database')
        indi = f'{itemEngine}.{itemDb}'
        if (indi not in added) and ((itemEngine != inst.name) or (itemDb != inst.database)):
            added.add(indi)
            dbName = GetDictDisplayName(itemEngine, itemDb)
            others.append({'language': e.get('language', ''), 'engine': itemEngine, 'database': itemDb,
                'dbName': f'{itemEngine} [{dbName}]'})

    try:
        definition = inst.definition(word, language)
        if not definition and language: #如果查询不到，尝试使用构词法词典获取词根
            hObj = InitHunspell(language)
            stem = GetWordStem(hObj, word)
            if stem:
                definition = inst.definition(stem, language) #再次查询

            if not definition:
                suggests = GetWordSuggestions(hObj, word)
                if suggests:
                    sugTxt = ' '.join([f'<a href="https://kindleear/entry/{s}" style="font-size:1.2em;font-weight:bold;margin:10px 20px 5px 0px">{s}</a>' 
                        for s in suggests])
                    definition = '<br/>'.join([_("No definitions found for '{}'.").format(word),
                        _("Did you mean?"), sugTxt])
            else:
                word = stem
    except Exception as e:
        #import traceback
        #traceback.print_exc()
        definition = f'Error:<br/>{e}'
    #print(json.dumps(definition)) #TODO
    return {'status': 'ok', 'word': word, 'definition': definition, 
        'dictname': str(inst), 'others': others}

#获取词典外挂的CSS
@bpReader.route("/reader/css/<path:path>", endpoint='ReaderDictCssRoute')
def ReaderDictCssRoute(path: str):
    dictDir = app.config.get('DICTIONARY_DIR')
    return send_from_directory(dictDir, path) if dictDir and os.path.isdir(dictDir) else ''

#构建Hunspell实例
#language: 语种代码，只有前两个字母
def InitHunspell(language):
    try:
        import dictionary
        import hunspell #type:ignore
    except Exception as e:
        #import traceback #TODO
        #default_log.warning(traceback.format_exc())
        return ''

    dictDir = app.config['DICTIONARY_DIR'] or ''
    morphDir = os.path.join(dictDir, 'morphology') if dictDir else ''
    dics = []
    if morphDir and os.path.isdir(morphDir):
        dics.extend([os.path.splitext(e)[0] for e in os.listdir(morphDir) if e.endswith('.dic') and e.startswith(language)])

    if dics:
        dic = dics[0]
    elif language.startswith('en'): #使用默认英语变形数据 en_US
        dic = 'en_US'
        morphDir = None
    else:
        return ''

    try:
        return hunspell.Hunspell(lang=dic, hunspell_data_dir=morphDir)
    except Exception as e:
        default_log.warning(f'Init hunspell failed: {e}')
        return None

#根据构词法获取词干
#hObj: hunspell 实例
#word: 要查询的单词
def GetWordStem(hObj, word) -> str:
    if not hObj:
        return ''

    stems = []
    try:
        stems = [s for s in hObj.stem(word) if s != word]
        default_log.debug(f'got stem tuple: {stems}')
    except Exception as e:
        default_log.warning(f'Get stem of "{word}" failed: {e}')

    stem = stems[0] if stems else ''
    if isinstance(stem, bytes):
        stem = stem.decode('utf-8')
    return stem

#获取单词的拼写建议
#hObj: hunspell 实例
#word: 要查询的单词
def GetWordSuggestions(hObj, word) -> list:
    if not hObj:
        return []

    try:
        return [s for s in hObj.suggest(word) if s != word]
    except Exception as e:
        print(e)
        return []

#将一个特定的文章制作成电子书推送
def PushSingleArticle(src: str, title: str, user: KeUser, userDir: str, language: str):
    if '..' in src:
        return _('Failed to push: {}').format('insecurity path expression')

    path = os.path.join(userDir, src).replace('\\', '/')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            html = f.read()
    except Exception as e:
        return _('Failed to push: {}').format(e)

    imgs = []
    dirName = os.path.dirname(path)
    soup = BeautifulSoup(html, 'lxml')
    #将css嵌入html
    css = []
    for tag in soup.head.find_all('link', attrs={'type': 'text/css', 'href': True}): #type:ignore
        try:
            with open(os.path.join(dirName, tag['href']), 'r', encoding='utf-8') as f:
                data = f.read()
        except:
            continue
        tag.extract()
        css.append(data)

    style = soup.head.find('style') #type:ignore
    if not style:
        style = soup.new_tag('style')
        style.string = '\n'.join(css)
        soup.head.append(style) #type:ignore
    else:
        style.string = '\n'.join(css) + '\n' + (style.string or '') #type:ignore
    
    for tag in soup.find_all('img', src=True): #读取图片，修正图片路径，从images目录里面移出
        src = tag['src']
        try:
            with open(os.path.join(dirName, src), 'rb') as f:
                data = f.read()
        except:
            tag.extract()
            continue

        if data:
            if src.startswith('images/'):
                src = src[7:]
            elif src.startswith('/images/'):
                src = src[8:]
            tag['src'] = src
            imgs.append((src, data))
        else:
            tag.extract()

    book = html_to_book(str(soup), title, user, imgs, language=language, options={'dont_save_webshelf': True})
    if book:
        send_to_kindle(user, title, book, fileWithTime=False)
        return 'ok'
    else:
        return _('Failed to create ebook.')

def clean_title(title: str) -> str:
    if not title:
        return ''
    # 剥离 001_ 或 01_ 数字前缀
    title = re.sub(r'^\d+_', '', title)
    # HTML/XML 实体反转义（如 &amp; -> &, &#39; -> '）
    title = html.unescape(title).strip()
    return title

#获取当前目录保存的所有电子书，返回一个列表[{date:, books: [{title:, articles:[{title:, src:}],},...]}, ]
def GetSavedOebList(sourceDir: str) -> list:
    if not os.path.isdir(sourceDir):
        return []

    ret = []
    for date in sorted(os.listdir(sourceDir), reverse=True):
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
            continue
        dateDir = os.path.join(sourceDir, date)
        if not os.path.isdir(dateDir):
            continue
        someDay = {'date': date, 'books': []}
        for book in sorted(os.listdir(dateDir), reverse=True):
            bookDir = os.path.join(dateDir, book)
            if not os.path.isdir(bookDir):
                continue
            opfFile = os.path.join(bookDir, 'content.opf')
            tocFile = os.path.join(bookDir, 'toc.ncx')
            prefix = f'{date}/{book}'
            meta = ExtractBookMeta(opfFile)
            articles = ExtractArticleList(tocFile, prefix)
            if meta and articles:
                bTitle = meta.get('title') or clean_title(book)
                someDay['books'].append({'title': bTitle, 'language': meta.get('language', 'en'), 
                    'bookDir': prefix, 'rawBookDir': book, 'articles': articles})
        if someDay['books']:
            ret.append(someDay)

    ret.sort(key=itemgetter('date'), reverse=True)
    return ret

#从content.opf里面提取文章元信息，返回一个字典 {title:,language:,}
def ExtractBookMeta(opfFile: str) -> dict:
    if not os.path.isfile(opfFile):
        return {}

    try:
        tree = etree.parse(opfFile)
    except Exception as e:
        default_log.warning(f"Error parsing Toc file: {opfFile} : {e}")
        return {}

    root = tree.getroot()
    ret = {}
    title = root.find('.//{*}title')
    if title is not None and title.text:
        ret['title'] = clean_title(title.text)
    lang = root.find('.//{*}language')
    if lang is not None and lang.text:
        ret['language'] = lang.text.strip()
    
    return ret

#从toc.ncx里面提取文章列表，返回一个字典列表 [{title:,'src':,}]
def ExtractArticleList(ncxFile: str, prefix: str) -> list:
    if not os.path.isfile(ncxFile):
        return []

    try:
        tree = etree.parse(ncxFile)
    except Exception as e:
        default_log.warning(f"Error parsing Toc file: {ncxFile} : {e}")
        return []

    root = tree.getroot()
    navPoints = root.findall('.//{*}navPoint')
    #只需要最低一层 navPoint
    ret = []
    for nav in [e for e in navPoints if (len(e.findall('.//{*}navPoint')) == 0)]:
        text = nav.find('.//{*}text')
        src = nav.find('.//{*}content')
        if text is not None and src is not None:
            raw_text = (text.text or '').strip()
            article_src = src.attrib.get('src', '')
            if raw_text and article_src:
                ret.append({'title': clean_title(raw_text), 'src': f'{prefix}/{article_src}'})
    return ret

#受限文章鉴权失败时渲染的优雅遮罩引导页
def render_article_lock_page(is_guest: bool):
    isZh = get_locale().startswith('zh')
    if is_guest:
        title = "免费试读已达上限 (5篇)" if isZh else "Free Trial Limit Reached (5 Articles)"
        msg = ("您已免费体验阅读 5 篇精选报道。登录/注册账号即可解锁每日 10 篇额度；开通 VIP 畅享全部精选媒体无限畅读与 30 天历史期刊归档。"
               if isZh else
               "You have completed your 5-article free guest trial. Sign up or log in to unlock 10 articles daily, or upgrade to VIP for unlimited reading across all media and 30-day archives.")
        btn1 = f'<a href="/login" target="_top" style="display:inline-block;padding:10px 22px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;margin:6px;">{"立即登录" if isZh else "Log In"}</a>'
        btn2 = f'<a href="/signup" target="_top" style="display:inline-block;padding:10px 22px;background:#10b981;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;margin:6px;">{"免费注册" if isZh else "Sign Up"}</a>'
        btn3 = f'<a href="/vip" target="_top" style="display:inline-block;padding:10px 22px;background:#f59e0b;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;margin:6px;">{"了解 VIP" if isZh else "VIP Info"}</a>'
        buttons = f'{btn1} {btn2} {btn3}'
    else:
        title = "VIP 会员专享深度报道" if isZh else "VIP Exclusive Article"
        msg = ("本文章为 VIP 会员专享深度内容。升级 VIP 会员即可解锁路透社、彭博社、BBC 等全平台媒体，畅享无限篇幅阅读与近 30 天历史期刊归档。"
               if isZh else
               "This article is exclusive to VIP members. Upgrade to VIP to unlock unlimited reading across all curated media and 30-day archives.")
        btn1 = f'<a href="/vip" target="_top" style="display:inline-block;padding:10px 24px;background:#f59e0b;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;margin:6px;">{"立即升级 VIP" if isZh else "Upgrade to VIP"}</a>'
        btn2 = f'<a href="/reader" target="_top" style="display:inline-block;padding:10px 20px;background:#6b7280;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;margin:6px;">{"返回书架" if isZh else "Back to Reader"}</a>'
        buttons = f'{btn1} {btn2}'

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{title}</title>
<style>
body {{ margin:0; padding:40px 20px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:#f8fafc; color:#1e293b; display:flex; align-items:center; justify-content:center; min-height:80vh; }}
.card {{ max-width:520px; width:100%; background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:32px 24px; text-align:center; box-shadow:0 4px 12px rgba(0,0,0,0.06); }}
.lock-icon {{ font-size:44px; margin-bottom:16px; display:inline-block; }}
h2 {{ margin:0 0 14px; font-size:22px; color:#0f172a; }}
p {{ font-size:15px; line-height:1.6; color:#64748b; margin:0 0 24px; }}
.actions {{ display:flex; flex-wrap:wrap; justify-content:center; gap:8px; }}
</style>
</head>
<body>
<div class="card">
  <div class="lock-icon">🔒</div>
  <h2>{title}</h2>
  <p>{msg}</p>
  <div class="actions">
    {buttons}
  </div>
</div>
</body>
</html>"""
    resp = make_response(html_content, 403)
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp
