import random
import threading

from telebot import types
from telebot.apihelper import ApiException

import config
from models import Session, User
from utils import bot, bot_id, logger, get_user

# store all scheduled jobs (threading.Timer) to be able to cancel them once needed
SCHEDULED_JOBS = {
    # captcha:chat_id:message_id: threading.Thread
}


def ban_bots(message):
    """Scans new members for bots,
    if there are user bots among new users -- kicks the bots
    and adds the rest of the users to the database
    """

    # If the members were invited by an admin, skips the bot detection
    admin_invited = message.from_user.id in config.admin_ids
    member_identifiers = {
        member.id: {
            "name": member.first_name,
            "username": member.username,
            "member": member
        }
        for member in message.new_chat_members
    }

    # Checks every new member
    for member in message.new_chat_members:
        # If new member is bot, kicks it out and moves on
        if member.is_bot:
            del member_identifiers[member.id]
            if member.id != bot_id and not admin_invited:
                bot.kick_chat_member(chat_id=config.chat_id, user_id=member.id)
                logger.info("Bot {} has been kicked out".format(get_user(member)))
    if config.CAPTCHA_ENABLED:
        throw_captcha(message, member_identifiers)
    else:
        for user_id, info in member_identifiers.items():
            add_user(info["member"])
            restrict(user_id)


def add_user(member):
    session = Session()
    if not session.query(User).get(member.id):
        user_obj = User(member.id)
        session.add(user_obj)
        logger.info(
            "User {} has joined the chat for the first time and "
            "has been successfully added to the database".format(get_user(member))
        )
    session.commit()
    session.close()


def construct_captcha_message(member_identifiers):
    mentions = []
    for user_id, info in member_identifiers.items():
        if info["username"] is not None:
            mention = "@" + info["username"]
        else:
            mention = f"[{info['name']}](tg://user?id={user_id})"
        mentions.append(mention)
    return config.captcha_message.format(names=", ".join(mentions), seconds=config.CAPTCHA_TIMEOUT)


def build_distinct_key(message):
    return f"captcha:{config.chat_id}:{message.message_id}"


def construct_captcha_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    human = types.InlineKeyboardButton(text="Я человек", callback_data="captcha_passed")
    robot = types.InlineKeyboardButton(text="Я робот", callback_data="captcha_failed")
    buttons = [human, robot]
    random.shuffle(buttons)
    keyboard.add(*buttons)
    return keyboard


def kick_member(member_id):
    bot.kick_chat_member(chat_id=config.chat_id, user_id=member_id)
    # un-ban user, so he can join again later and solve the captcha again
    bot.unban_chat_member(chat_id=config.chat_id, user_id=member_id)


def kick_users_with_failed_captcha(distinct_key, captcha_message_id):
    user_ids = list(map(int, config.r.smembers(distinct_key)))
    for user_id in user_ids:
        kick_member(user_id)
    config.r.delete(distinct_key)
    del SCHEDULED_JOBS[distinct_key]
    bot.delete_message(config.chat_id, captcha_message_id)


def throw_captcha(message, member_identifiers):
    restricted_users = []
    for member_id in member_identifiers:
        try:
            bot.restrict_chat_member(chat_id=config.chat_id, user_id=member_id, can_send_messages=False)
            restricted_users.append(member_id)
        except ApiException:
            continue
    # do further actions only with successfully restricted users
    member_identifiers = {user_id: member_identifiers[user_id] for user_id in restricted_users}
    if not member_identifiers:
        return
    captcha_text = construct_captcha_message(member_identifiers)
    captcha_keyboard = construct_captcha_keyboard()
    captcha_message = bot.send_message(
        message.chat.id,
        text=captcha_text,
        parse_mode="Markdown",
        reply_markup=captcha_keyboard
    )
    # add restricted uses to redis for further kick/release
    distinct_key = build_distinct_key(captcha_message)
    config.r.sadd(distinct_key, *member_identifiers.keys())
    # schedule task for deleting all users with failed captcha
    restriction_job = threading.Timer(
        interval=config.CAPTCHA_TIMEOUT,
        function=kick_users_with_failed_captcha,
        kwargs={
            "distinct_key": distinct_key,
            "captcha_message_id": captcha_message.message_id
        }
    )
    SCHEDULED_JOBS[distinct_key] = restriction_job
    restriction_job.start()


def restrict(user_id):
    """
    Forbid new users from sending inline bot spam.
    """
    session = Session()
    member = session.query(User).filter(User.user_id == user_id).one_or_none()

    # Skip restriction if user already have messages.
    if member is not None and member.msg_count < 10:
        bot.restrict_chat_member(
            chat_id=config.chat_id,
            user_id=user_id,
            can_send_other_messages=False,
            can_send_messages=True,
            can_send_media_messages=True,
            can_add_web_page_previews=True,
        )
    session.close()
