#!/usr/bin/env python
# coding: utf-8
#
#
#Current setup requires coindata to be fetched from websites only once
#a request has been made. marketprices have to be fetched again every few
#minutes, which means that certain users will experience a lot of lag while
#the data is retrieved from websites. Ideally this process would be handled by
#a dedicated backend server and stored in memcache, however for the purposes of
#this project that was considered too expensive under Google's pricing options.
#Perhaps moving to Amazon AWS might be a better solution, especially if the app
#will be scaled to include more data points.
#
import os
import time
import webapp2
import re
import jinja2
from string import letters
import string
import json
from google.appengine.ext import db
from google.appengine.api import memcache
from xml.dom import minidom
import logging
import pickle
import operator
import sys
sys.path.insert(0, 'requests')
import requests
from requests.exceptions import ConnectionError

template_dir = os.path.join(os.path.dirname(__file__), 'templates')
jinja_env = jinja2.Environment(loader = jinja2.FileSystemLoader(template_dir), autoescape = True)
BLOCKS_PER_DAY = {"LTC":576, "BTC":144, "AUR":288, "DOGE":1440, "PPC":144, "NMC":144, "FTC":1440}
COIN_URLS = {"BTC":"http://www.bitcoin.org/","LTC":"http://litecoin.org/", "DOGE":"http://dogecoin.com/",
"VTC":"http://vertcoin.org/", "PPC":"http://www.peercoin.net/", "NMC":"http://namecoin.info/",
"MAX":"http://www.maxcoin.co.uk/", "FTC":"http://feathercoin.com/", "XPM":"http://primecoin.io/",
"DRK":"http://www.darkcoin.io/", "WDC":"http://www.worldcoinfoundation.org/", "NVC":"http://novacoin.org/",
"POT":"http://potcoin.info/", "ANC":"https://anoncoin.net/", "DGB":"http://www.digibyte.co/",
"RDD":"http://www.reddcoin.com/", "HBN":"http://hobonickels.info/", "CRYPT":"http://cryptco.org/",
"NAUT":"http://www.nautiluscoin.com/",
"VIA":"http://viacoin.org/"}
SCAMS = ["CRYPT", "CGB", "IFC", "IXC", "RZR"]
NOT_MINED = ['VOOT']

class Handler(webapp2.RequestHandler):
	def write(self, *a, **kw):
		self.response.out.write(*a, **kw)

	def render_str(self, template, **params):
		t = jinja_env.get_template(template)
		return t.render(params)

	def render(self, template, **kw):
		self.write(self.render_str(template, **kw))
	def valid_user(self):
		user = self.request.cookies.get('user')
		if user:
			user = check_secure_val(self.request.cookies.get('user'))
			return user
		else:
			return False

def get_url(url):
	#uses requests to fetch urls, catches exceptions
	try:
		r = requests.get(url, timeout=20)
	except (ConnectionError, 'Timeout') as e:
		r = False  #'ConnectionFailed'
		return r
	try:
		r.raise_for_status()
	except requests.exceptions.HTTPError:
		r = False
	return r

def marketdata(update=False):
	#finds the current exchange rate of all coins traded on Cryptsy.
	#coins are traded relative to BTC, LTC, and USD, depending on the coin.
	#Because the BTC/USD exchange rate differs on each exchange, coindesk.com
	#is used to provide an averaged value. Later all exchage rates will
	#be converted into /USD.
	url = "http://pubapi.cryptsy.com/api.php?method=marketdatav2"
	data = memcache.get("marketdata")
	if data == None or update:
		data = get_url(url)
		if data:
			data = data.json()
			cacheTime = int(time.time())
			newData = {}
			for i in data['return']['markets']:
				if data['return']['markets'][i]['lasttradeprice']:
					newData[i] = data['return']['markets'][i]['lasttradeprice']
			BTC_USD = get_url('http://api.coindesk.com/v1/bpi/currentprice/USD.json')
			BTC_USD = BTC_USD.json()
			BTC_USD = float(BTC_USD['bpi']['USD']['rate'])
			newData['BTC/USD'] = BTC_USD
			memcache.set("marketdata", [newData, cacheTime])
			return newData, cacheTime
		else:
			return False
	if ((int(time.time()) - data[1]) > 600):
		return marketdata(update=True)
	else:
		return data

def coins_per_block(update=False):
	#finds the number of coins awarded per block, block time in seconds,
	#coin name and exchange rate for each coin. results return in dictionary of
	#arrays, sorted by cointag (eg "BTC")
	url = "http://www.coinwarz.com/v1/api/profitability/?apikey=25ea0abd0d334c059fff797b5ea80272&algo=all"
	data = memcache.get("coindata")
	if data == None or update:
		data = get_url(url)
		if data:
			data = data.json()
			cacheTime = int(time.time())
			newData = {}
			i = 0
			while i < len(data['Data']):
				if data['Data'][i]["CoinTag"] not in ['ZEIT', 'CGB', "IFC", 'VOOT']:
					newData[data['Data'][i]["CoinTag"]] = [data['Data'][i]['BlockReward'], data['Data'][i]['BlockTimeInSeconds'], data['Data'][i]['CoinName'] ,data['Data'][i]['ExchangeRate']]
				i += 1
			amounts = other_amounts()
			if amounts:
				for i in amounts:
					newData[i] = amounts[i]
			memcache.set("coindata", [newData, cacheTime])
			if memcache.get("CPB_last_attempt"):
				memcache.delete("CPB_last_attempt")
			return newData, cacheTime
		else:
			return False
	if ((int(time.time()) - data[1]) > 86400):
		if memcache.get("CPB_last_attempt") and (int(time.time()) - memcache.get("CPB_last_attempt") < 21600):
			return data
		else:
			return coins_per_block(update=True)
	else:
		return data

def other_amounts():
	#for coins that don't have a simple/static
	#number of coins awarded per block
	#this function finds the current reward per block for each coin
	amounts = {}
	r = get_url("https://coinplorer.com/XPM")
	if r:
		text = r.text
		reg = re.compile("(?s)Block reward:</td>.+<td>(\d+\.\d+)</td>")
		match = re.search(reg, text)
#		if match:
		reward = match.group(1)
		amounts["XPM"] = [float(reward), 60, 'Primecoin']
	f = get_url("https://coinplorer.com/FLO")
	if f:
		text = f.text
		reg = re.compile("(?s)Block reward:</td>.+<td>(\d+\.\d+)</td>")
		match = re.search(reg, text)
#		if match:
		reward = match.group(1)
		amounts["FLO"] = [float(reward), 40, 'Florincoin']
	return amounts

def get_and_verify_sources():
	#gets all of the data needed for the rankings from the respective websites
	data = marketdata()
	coins_block = coins_per_block()
	if data == False:
		return False
	elif coins_block == False:
		if memcache.get("coindata") and (int(time.time()) - memcache.get("coindata")[1] < 604800):
			memcache.set("CPB_last_attempt", int(time.time()))
			coins_block = memcache.get("coindata")
		else:
			return False
	return data, coins_block

class CoinRanker(Handler):
	#main fn, called when user requests page.
	def get(self):
		source = get_and_verify_sources()
		if source:
			data, coins_block = source
			USD_data = USD_price_calc(data)
			coin_data = {} #USD per day
			#debug use
			unknown_price = []
			unknown_amount = []
			#end debug
			for i in coins_block[0]:
				if USD_data.get(i + "/USD"):
					coin_data[i] = coins_block[0][i][0] * (86400 / coins_block[0][i][1]) * USD_data[i + "/USD"]
				else: unknown_price = unknown_price + [i]
			#more debug
			for j in USD_data:
				t = False
				for k in coins_block[0]:
					if k == j[:-4]:
						t = True
						break
				if t == False:
					unknown_amount = unknown_amount + [j[:-4]]
			#end debug
			# Find total USD per day
			total = 0.0
			for item in coin_data:
				total += coin_data[item]
			totalUSD = "${:,.2f}".format(total)
			#sort and format
			coin_data = sorted(coin_data.iteritems(), key=operator.itemgetter(1), reverse=True)
			l = 0
			while l < len(coin_data):
				coin_data[l] = [coins_block[0][coin_data[l][0]][2], "${:,.2f}".format(coin_data[l][1]), coin_data[l][0], "%.7f" % (coin_data[l][1] * 100 / total)] # CoinName, USD per Day, CoinTag, % of total USD
				l += 1
			#self.write(unknown_amount)
			#self.write(unknown_price)
			self.render('bitcoin_data.html', data=coin_data, coin_urls=COIN_URLS, scams=SCAMS, time=time.strftime("%b %d %Y", time.gmtime()), totalUSD=totalUSD)
		else:
			self.write("Error: Price Data Unavialable")
			#self.write(marketdata())



def USD_price_calc(marketdata):
	BTC_USD = marketdata[0]['BTC/USD']
	LTC_USD = float(marketdata[0]['LTC/BTC']) * BTC_USD
	marketdata = marketdata[0]
	USD_prices = {}
	for i in marketdata:
		if i[-3:] == 'BTC':
			if i[:-3] + 'USD' not in USD_prices:
				USD_prices[i[:-3] + 'USD'] = float(marketdata[i]) * BTC_USD
		elif i[-3:] == 'LTC':
			if i[:-3] + 'USD' not in USD_prices:
				USD_prices[i[:-3] + 'USD'] = float(marketdata[i]) * LTC_USD
		elif i[-3:] == 'USD':
			USD_prices[i] = float(marketdata[i])
	USD_prices['BTC/USD'] = BTC_USD
#	for i in USD_prices:
#		if USD_prices[i] < 1:
#		USD_prices[i] = format(USD_prices[i], 'f', precision=8).rstrip('0')
	return USD_prices

class GetJSON(Handler):
	def get(self):
		self.response.headers['Content-Type'] = 'application/json'
		source = get_and_verify_sources()
		if source:
			data, coins_block = source
			USD_data = USD_price_calc(data)
			coin_data = {} #USD per day
			for i in coins_block[0]:
				if USD_data.get(i + "/USD"):
					coin_data[i] = coins_block[0][i][0] * (86400 / coins_block[0][i][1]) * USD_data[i + "/USD"]

			total = 0.0
			for item in coin_data:
				total += coin_data[item]
			totalUSD = round(total, 2)

			coin_data = sorted(coin_data.iteritems(), key=operator.itemgetter(1), reverse=True)

			l = 0
			while l < len(coin_data):
				coin_data[l] = {"CoinName":coins_block[0][coin_data[l][0]][2], "USD_per_day":round(coin_data[l][1], 2), "CoinTag":coin_data[l][0], "PercentOfTotalUSD":(coin_data[l][1] / total)} # CoinName, USD per Day, CoinTag, % of total USD
				l += 1
			finalJSON = {"success":1, "return":coin_data}
			self.write(finalJSON)
		else:
			finalJSON = {"success":0}
			self.write(finalJSON)

app = webapp2.WSGIApplication([
    (r'/', CoinRanker),
    (r'/json', GetJSON)
], debug=True)
