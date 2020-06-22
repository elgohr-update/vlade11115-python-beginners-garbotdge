#!/usr/bin/env python3
import datetime
import time

from requests.exceptions import ConnectionError, ReadTimeout

import config
from commands import monitor, new_users, report
from config import r
from utils import (
    bot,
    get_admins,
    get_user,
    logger,
    validate_command,
    watching_newcomers,
    make_paste,
    validate_paste,
    validate_document,
    perfect_justice,
)


# Handler for banning invited user bots
@bot.message_handler(content_types=["new_chat_members"])
def ban_invited_bots(message):
    if not validate_command(message, check_isinchat=True):
        return

    new_users.ban_bots(message)
    bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)


# Handler for initializing a chat's admin
@bot.message_handler(commands=["start"])
def start_msg(message):
    if not validate_command(message, check_isprivate=True, check_isadmin=True):
        return

    start_text = (
        "Вы действительно админ чата {}.\n"
        "Значит я вам сюда буду пересылать пользовальские репорты "
        "и подозрительные сообщения.".format(config.chat_name)
    )
    bot.reply_to(message, start_text)
    logger.info(
        "Admin {} has initiated a chat with the bot".format(get_user(message.from_user))
    )


# Handler for updating a list of chat's admins to memory
@bot.message_handler(commands=["admins"])
def update_admin_list(message):
    if not validate_command(message, check_isprivate=True, check_isadmin=True):
        return

    config.admin_ids = get_admins(config.chat_id)
    admins = ",\n".join([str(admin) for admin in config.admin_ids])
    update_text = "Список администратов успешно обновлён:\n{}".format(admins)
    bot.reply_to(message, update_text)
    logger.info(
        "Admin {} has updated the admin list".format(get_user(message.from_user))
    )


@bot.message_handler(func=validate_paste)
def paste(message):
    source = message.reply_to_message
    source_text = source.text or source.caption
    new_paste = make_paste(source_text, source.from_user.first_name)
    if not new_paste:
        return
    bot.reply_to(source, text=new_paste, disable_web_page_preview=True)
    bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    logger.info(
        "User {0} has requested a paste version of a message {1}".format(
            get_user(message.from_user), message.reply_to_message.message_id
        )
    )


@bot.message_handler(
    func=lambda m: m.text and m.text.lower().startswith("!meta") and m.reply_to_message
)
def meta_question(message):
    source = message.reply_to_message
    bot.reply_to(source, text=config.nometa)
    bot.delete_message(message_id=message.message_id, chat_id=message.chat.id)


# Handler for reporting spam to a chat's admins
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("!report"))
def report_to_admins(message):
    if not validate_command(message, check_isreply=True):
        bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        return
    report.my_report(message)


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("!justify"))
def justify(message):
    source = message.reply_to_message

    if not validate_command(message, check_isadmin=True):
        return

    if perfect_justice():
        bot.reply_to(source, text="Bang! РО на день")
        tomorrow = datetime.date.today() + datetime.timedelta(1)
        unix_time = tomorrow.strftime("%s")
        bot.restrict_chat_member(
            chat_id=config.chat_id, user_id=source.from_user.id, until_date=unix_time
        )
    else:
        bot.reply_to(source, text="Lucky one")

    bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)


@bot.message_handler(content_types=["document"], func=validate_document)
def document_to_paste(message):
    document = message.document
    file_info = bot.get_file(document.file_id)
    try:
        file_content = bot.download_file(file_info.file_path).decode()
    except UnicodeDecodeError:
        logger.info("Can't decode file content")
        return
    new_paste = make_paste(
        file_content, message.from_user.first_name, document.file_name
    )
    if not new_paste:
        return
    bot.reply_to(message, text=new_paste)
    logger.info(
        "Successfully created a paste of a document from message {}".format(
            message.message_id
        )
    )


# Handler for monitoring messages of users who have <= 10 posts
@bot.message_handler(
    content_types=[
        "text",
        "sticker",
        "photo",
        "audio",
        "document",
        "video",
        "voice",
        "video_note",
    ]
)
def scan_for_spam(message):
    messages_count = watching_newcomers(message.from_user.id)

    if messages_count < 10:
        monitor.scan_contents(message)
    elif messages_count == 10:
        bot.restrict_chat_member(
            chat_id=config.chat_id,
            user_id=message.from_user.id,
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("captcha"))
def captcha_handler(call):
    user_id = call.from_user.id
    distinct_key = new_users.build_distinct_key(call.message)
    if not config.r.exists(distinct_key):
        return  # captcha message doesn't exists
    user_belong_to_this_captcha = config.r.srem(distinct_key, user_id)
    if not user_belong_to_this_captcha:
        return bot.answer_callback_query(call.id, text="Это сообщение не для тебя.")
    restricted_users_left = config.r.scard(distinct_key)
    if not restricted_users_left:
        # if not users left for current captcha, remove captcha message
        config.r.delete(distinct_key)
        bot.delete_message(config.chat_id, call.message.message_id)
        # cancel restriction job because all users already solved the captcha
        new_users.SCHEDULED_JOBS[new_users.build_distinct_key(call.message)].cancel()
    if call.data == "captcha_passed":
        bot.restrict_chat_member(chat_id=config.chat_id, user_id=user_id, can_send_messages=True)
        new_users.add_user(call.from_user)
        new_users.restrict(user_id)
        bot.answer_callback_query(call.id, text="Welcome!")
    else:
        new_users.kick_member(user_id)


@bot.message_handler(commands=["captcha"], func=lambda m: m.from_user.id in config.admin_ids)
def captcha_switcher(message):
    config.CAPTCHA_ENABLED = not config.CAPTCHA_ENABLED
    bot.reply_to(message, text=f"Captcha: {'enabled' if config.CAPTCHA_ENABLED else 'disabled'}")


# Callback handler for the admins' judgment
@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    user_id = int(call.message.text.split(" ")[3])
    message_id = int(call.message.text.split(" ")[7])

    if not r.get(message_id):
        bot.answer_callback_query(call.id, text="Это сообщение уже отмодерировано.")
        return

    r.delete(message_id)
    if call.data == "ban":
        bot.kick_chat_member(chat_id=config.chat_id, user_id=user_id)
    elif call.data == "release":
        bot.restrict_chat_member(
            chat_id=config.chat_id,
            user_id=user_id,
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )
    bot.answer_callback_query(call.id, text="OK.")


# Entry point
if __name__ == "__main__":
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
            break
        except ConnectionError:
            logger.exception("ConnectionError")
            time.sleep(15)
        except ReadTimeout:
            logger.exception("ReadTimeout")
            time.sleep(10)
