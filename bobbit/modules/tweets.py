# tweets.py

import base64
import dbm.gnu
import collections
import logging
import os
import time
import yaml

from bobbit.message import Message
from bobbit.utils   import shorten_url, strip_html

# Metadata

NAME     = 'tweets'
ENABLE   = True
TYPE     = 'timer'
TEMPLATE = 'From {color}{green}{user}{color} twitter: {bold}{status}{bold} @ {color}{blue}{link}{color}'

# Constants

TWITTER_USER_TIMELINE_URL = 'https://api.twitter.com/1.1/statuses/user_timeline.json'
TWITTER_OAUTH2_TOKEN_URL  = 'https://api.twitter.com/oauth2/token'

# Utility

async def get_access_token(http_client, key, secret):
    url     = TWITTER_OAUTH2_TOKEN_URL
    token   = base64.b64encode(f'{key}:{secret}'.encode())
    headers = {
        'Authorization' : 'Basic ' + token.decode(),
        'Content-Type'  : 'application/x-www-form-urlencoded;charset=UTF-8'
    }
    params  = {
        'grant_type'    : 'client_credentials'
    }

    async with http_client.post(url, headers=headers, params=params) as response:
        data = await response.json()
        try:
            return data['access_token']
        except KeyError as e:
            logging.exception(e)
            logging.debug(data)
            return None

async def get_user_timeline(http_client, user, since_id, access_token):
    url     = TWITTER_USER_TIMELINE_URL
    headers = {
        'Authorization' : 'Bearer ' + access_token,
    }
    params = {
        'screen_name'    : user,
        'exclude_replies': 'true',
        'trim_user'      : 'true',
        'include_rts'    : 'false',
        'since_id'       : since_id,
        'count'          : 10,
    }

    async with http_client.get(url, headers=headers, params=params) as response:
        return await response.json()

async def process_feed(http_client, feed, cache, access_token):
    user     = feed['user']
    channels = feed['channels']
    pattern  = feed.get('pattern', '')
    since_id = int(cache.get('since_id', 1))
    statuses = await get_user_timeline(http_client, user, since_id, access_token)

    logging.info('Processing %s timeline...', user)
    for status in statuses:
        # Skip if status does not contain pattern
        status_text = strip_html(status['text'])
        if pattern and pattern not in status_text:
            logging.debug("Skipping status from %s (doesn't match pattern)", user)
            continue

        # Skip if status is in cache
        status_key = '{}/{}'.format(user.lower(), status['id'])
        if status_key in cache:
            logging.debug('Skipping status from %s (in cache)', user)
            continue

        # Add status to entries
        logging.debug('Recording status from %s: %s', user, status_text)
        yield {
            'status'    : status_text.replace('\n', ' '),
            'channels'  : channels,
            'status_key': status_key,
            'status_id' : status['id'],
            'link'      : 'https://twitter.com/{}/status/{}'.format(user, status['id']),
        }

# Timer

async def tweets_timer(bot):
    logging.info('Tweets timer starting...')

    # Read configuration
    try:
        config_path      = os.path.join(bot.config.config_dir, 'tweets.yaml')
        tweets_config    = yaml.safe_load(open(config_path))
        templates        = tweets_config.get('templates', {})
        default_template = templates.get('default', TEMPLATE)
    except (IOError, OSError) as e:
        logging.warning(e)
        return

    # Get access token
    access_token = await get_access_token(
        bot.http_client,
        tweets_config['consumer_key'],
        tweets_config['consumer_secret'],
    )

    # Read tweets
    entries    = collections.defaultdict(list)
    cache_path = os.path.join(bot.config.config_dir, 'tweets.cache')

    with dbm.open(cache_path, 'c') as cache:
        logging.debug('Processing tweets...')
        for feed in tweets_config['feeds']:
            user = feed['user']
            try:
                async for tweet_entry in process_feed(bot.http_client, feed, cache, access_token):
                    entries[user].append(tweet_entry)
            except Exception as e:
                logging.exception(e)

        logging.debug('Delivering tweets...')
        for user, entries in entries.items():
            for entry in entries:
                status      = entry['status']
                channels    = entry['channels']
                status_key  = entry['status_key']
                status_id   = entry['status_id']
                link        = await shorten_url(bot.http_client, entry['link'])

                # Send each entry to the appropriate channel
                for channel in channels:
                    template = templates.get(channel, default_template)
                    await bot.outgoing.put(Message(
                        channel = channel,
                        body    = bot.client.format_text(
                            template,
                            user   = user,
                            status = status,
                            link   = link,
                        )
                    ))

                # Mark entry as delivered
                logging.info('Delivered %s from %s to %s', status, user, ', '.join(channels))
                cache['since_id'] = str(max(int(cache.get('since_id', 1)), status_id))
                cache[status_key] = str(time.time())

# Register

def register(bot):
    try:
        config_path   = os.path.join(bot.config.config_dir, 'tweets.yaml')
        tweets_config = yaml.safe_load(open(config_path))
        timeout       = tweets_config.get('timeout', 5*60)
    except (IOError, OSError) as e:
        logging.warning(e)
        return []

    return (
        ('timer', timeout, tweets_timer),
    )

# vim: set sts=4 sw=4 ts=8 expandtab ft=python:
