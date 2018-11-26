#/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import re
import shelve
from datetime import datetime

import telebot

import config

# Initializes the bot
bot = telebot.TeleBot(config.bot_token, threaded=False)

# Initializes the logger
logger = telebot.logger
telebot.logger.setLevel(logging.INFO)


def get_user(user):
    """Returns a string with user's info in
    'ID First_name Last_name (@user_name)' format
    """

    user_info = '{0} {1}'.format(user.id, user.first_name)
    if user.last_name:
        user_info += ' {}'.format(user.last_name)
    if user.username:
        user_info += ' (@{})'.format(user.username)
    return user_info


def validate_command(message, check_isprivate=False, check_isinchat=False, check_isreply=False,\
                        check_isadmin=False):
    """Checks whether a command was called properly
    """

    if check_isprivate and message.chat.type != 'private':
        logger.info("User {0} called {1} not in a private chat. Aborting".\
                        format(get_user(message.from_user), message.text.split(' ')[0]))
        return False

    if check_isinchat and message.chat.id != config.chat_id:
        logger.info("We are not in our chat. Aborting")
        return False

    if check_isreply and getattr(message, 'reply_to_message') is None:
        logger.info("User {0} called {1} the wrong way".\
                        format(get_user(message.from_user), message.text.split(' ')[0]))
        return False

    if check_isadmin and message.from_user.id not in config.admin_ids:
        logger.info("User {0} tried to call {1}. Aborting".\
                        format(get_user(message.from_user), message.text.split(' ')[0]))
        return False 

    return True


def watching_newcommers(user_id):
    """Checks if a user with user_id that has posted a message requires scanning
    i.e. whether the user has posted less than 10 messages
    """

    with shelve.open(config.data_name, 'c', writeback=True) as data:
        data['members'] = {} if not data.get('members') else data['members']
        if not data['members'].get(user_id):
            data['members'][user_id] = 0
        elif data['members'][user_id] > 10:
            return False

        data['members'][user_id] += 1
        return True
