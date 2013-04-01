#coding=utf8


import datetime
import json
import logging
import re
import string
import sys
import urllib
from google.appengine.api.labs import taskqueue
from google.appengine.api import urlfetch;
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
import stock


class UpdateStockInfoHandler(webapp.RequestHandler):
    
    def get(self):
        taskqueue.add(url='/tasks/updateallmarketcapital')
        print 'OK'
        
        
class UpdateAllMarketCapitalHandler(webapp.RequestHandler):

    def __getExchangeRate(self, area):
        url = "http://download.finance.yahoo.com/d/quotes.html?s=%sCNY=X&f=l1" % area
        result = urlfetch.fetch(url=url)
        if result.status_code == 200:
            return result.content.strip()
    
    def __get_page_content(self):
        url = 'https://www.google.com.hk/finance?output=json&start=0&num=10000&noIL=1&q=[%28%28exchange%20%3D%3D%20%22SHE%22%29%20%7C%20%28exchange%20%3D%3D%20%22SHA%22%29%29%20%26%20%28market_cap%20%3E%3D%200%29%20%26%20%28market_cap%20%3C%3D%2010000000000000000%29]&restype=company&gl=cn'
        result = urlfetch.fetch(url=url)
        if result.status_code == 200:
            data = result.content
            data = data.replace('&quot;', '\\"').replace('&amp;', '&').replace('&gt;','>').replace('&lt;', '<')
            data = data.replace('\\x22','\\"').replace('\\x26','&').replace('\\x3E','>').replace('\\x3C','<')
            data = data.replace('\\x27','\'').replace('\\x2F','\/').replace('\\x3B',';')
            return data
        
    def post(self):
        usd = self.__getExchangeRate('USD')
        hkd = self.__getExchangeRate('HKD')
        data = self.__get_page_content()
        data = json.loads(data)
        count=0
        for stock in data['searchresults']:
            if stock['ticker'].find('399')==0 or stock['ticker'].find('000')==0 and stock['exchange']=='SHA':
                continue
            taskqueue.add(url='/tasks/updatesinglemarketcapital',
                          #queue_name='queue'+str(count % 10),
                          params={'ticker' : stock['ticker'],
                                  'title' : stock['title'],
                                  'exchange' : stock['exchange'],
                                  'local_currency_symbol' : stock['local_currency_symbol'],
                                  'value' : stock['columns'][0]['value'],
                                  'usd' : usd,
                                  'hkd' : hkd})
            count += 1
        

class UpdateSingleMarketCapitalHandler(webapp.RequestHandler):
    
    def __get_page_content(self, ticker, exchange):
        if exchange == 'SHA':
            query = 'sh' + ticker
        elif exchange == 'SHE':
            query = 'sz' + ticker
        else:
            query = ''
            logging.warn('Error exchange: ' + exchange)
        url = "http://qt.gtimg.cn/S?q=" + query
        result = urlfetch.fetch(url=url)
        if result.status_code == 200:
            data = result.content
            data = data.split('~')
            return string.atof(data[len(data) - 5]) * 100000000
        return 0
        
    def __change_unit(self, symbol):
        symbol = symbol.encode('UTF-8')
        if symbol.find('-') > -1:
            return 0
        m = re.search(r'\d+\.\d*', symbol)
        number = string.atof(symbol[m.start() : m.end()])
        unit = symbol[m.end():]
        if unit == '亿':
            return number * 10000 * 10000
        elif unit == '万':
            return number * 10000
        elif unit == '万亿':
            return number * 10000 * 10000 * 10000
        else:
            logging.warn('unit is wrong')
            return 0
        
    def __get_market_capital(self):
        ticker = self.request.get('ticker')
        exchange = self.request.get('exchange')
        local_currency_symbol = self.request.get('local_currency_symbol')
        usd = self.request.get('usd')
        hkd = self.request.get('hkd')
        value = self.__get_page_content(ticker, exchange)
        if value == 0:
            value = self.__change_unit(self.request.get('value'))
        if local_currency_symbol.encode('UTF-8') == "￥":
            rate = 1
        elif local_currency_symbol == 'US$':
            rate = string.atof(usd)
        elif local_currency_symbol == 'HK$':
            rate = string.atof(hkd)
        elif local_currency_symbol == '-' and ticker.find('900') == -1 and ticker.find('200') == -1:
            rate = 1
        else:
            rate = 0
            logging.warn("Invalid Exchangerate: %s %s" % (ticker, local_currency_symbol))
        return value * rate
    
    def __update_market_capital(self, market_capital):
        ticker = self.request.get('ticker')
        title = self.request.get('title')
        s = stock.Stock.get_or_insert(ticker)
        s.title = title
        s.market_capital = market_capital
        s.put()
    
    
    def post(self):
        ticker = self.request.get('ticker')
        market_capital = self.__get_market_capital()
        self.__update_market_capital(market_capital)
        taskqueue.add(url='/tasks/updateearnings', params={'ticker' : stock['ticker']})
        

class UpdateEarningsHandler(webapp.RequestHandler):
    
    def __get_page_content(self, url):
        result = urlfetch.fetch(url=url)
        if result.status_code == 200:
            data = result.content
        map = {}
        lines = data.decode('GBK').encode('UTF-8').split('\n')
        for line in lines:
            fields = line.split('\t')
            for i in range(len(fields) - 2):
                if i + 1 not in map:
                    map[i + 1] = {}
                map[i + 1][fields[0]] = fields[i + 1]
        results = {}
        for k in map:
            if '报表日期' in map[k]:
                results[map[k]['报表日期']] = map[k]
        return results
    
    def __get_profit_earnings(self):
        ticker = self.request.get('ticker')
        url = "http://money.finance.sina.com.cn/corp/go.php/vDOWN_ProfitStatement/displaytype/4/stockid/%s/ctrl/all.phtml" % (ticker)
        return self.__get_page_content(url)
    
    def __get_balance_earnings(self):
        ticker = self.request.get('ticker')
        url = "http://money.finance.sina.com.cn/corp/go.php/vDOWN_BalanceSheet/displaytype/4/stockid/%s/ctrl/all.phtml" % (ticker)
        return self.__get_page_content(url)
    
    def __update_earnings(self):
        balance = self.__get_balance_earnings()
        profit = self.__get_profit_earnings()
        year = datetime.date.today().year
        for i in range(3):
            earnings_date = self.__get_recent_earnings_date(year - i, balance, profit)
            if earnings_date is None:
                continue
        if earnings_date is None:
            logging.warn('There is no earnings date')
        else:
            print earnings_date
        
    def __get_recent_earnings_date(self, year, balance, profit):
        q4 = datetime.date(year=year, month=12, day=31)
        q3 = datetime.date(year=year, month=9, day=30)
        q2 = datetime.date(year=year, month=6, day=30)
        q1 = datetime.date(year=year, month=3, day=31)
        if q4.strftime('%Y%m%d') in balance and q4.strftime('%Y%m%d') in profit:
            return q4
        elif q4.replace(year=year - 1).strftime('%Y%m%d') in balance and q4.replace(year=year - 1).strftime('%Y%m%d') in profit:
            if q3.strftime('%Y%m%d') in balance and q3.strftime('%Y%m%d') in profit and q3.replace(year=year - 1).strftime('%Y%m%d') in balance and q3.replace(year=year - 1).strftime('%Y%m%d') in profit:
                return q3
            elif q2.strftime('%Y%m%d') in balance and q2.strftime('%Y%m%d') in profit and q2.replace(year=year - 1).strftime('%Y%m%d') in balance and q2.replace(year=year - 1).strftime('%Y%m%d') in profit:
                return q2
            elif q1.strftime('%Y%m%d') in balance and q1.strftime('%Y%m%d') in profit and q1.replace(year=year - 1).strftime('%Y%m%d') in balance and q1.replace(year=year - 1).strftime('%Y%m%d') in profit:
                return q1
            else:
                return None
        else:
            return None
    
    def get(self):
        self.__update_earnings()
        
application = webapp.WSGIApplication([('/tasks/updatestockinfo', UpdateStockInfoHandler),
                                      ('/tasks/updateallmarketcapital', UpdateAllMarketCapitalHandler),
                                      ('/tasks/updatesinglemarketcapital', UpdateSingleMarketCapitalHandler),
                                      ('/tasks/updateearnings', UpdateEarningsHandler)],
                                     debug=True)


def main():
    logging.getLogger().setLevel(logging.DEBUG)
    run_wsgi_app(application)
    
    
if __name__ == '__main__':
    main()