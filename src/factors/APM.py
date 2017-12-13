#!/usr/bin/env/ python3
# -*- coding: utf-8 -*-
# @Filename: APM
# @Date:   : 2017-12-09 22:44
# @Author  : YuJun
# @Email   : yujun_mail@163.com


from src.factors.factor import Factor
import src.factors.cons as factor_ct
from src.util.utils import Utils
from src.util.dataapi.CDataHandler import CDataHandler
import os
import numpy as np
from pandas import DataFrame
from pandas import Series
import statsmodels.api as sm
import datetime
import logging
from multiprocessing import Pool, Manager

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(filename)s[line:%(lineno)d] - %(levelname)s: %(message)s')


class APM(Factor):
    """APM因子类"""
    __days = factor_ct.APM_CT.days_num      # 读取过去多少天的分钟行情数据进行因子载荷计算
    _db_file = os.path.join(factor_ct.FACTOR_DB.db_path, factor_ct.APM_CT.db_file)      # 因子对应数据库文件名

    @classmethod
    def _calc_factor_loading(cls, code, calc_date):
        """
        计算指定日期、指定个股APM因子的stat统计量
        --------
        :param code: string
            个股代码，如600000
        :param calc_date: datetime-like, str
            因子载荷计算日期，格式YYYY-MM-DD
        :return: float
        --------
            stat统计量，计算APM因子载荷的中间变量
        """
        # 1.取得过去40个交易日序列，交易日按降序排列
        calc_date = Utils.to_date(calc_date)
        trading_days = Utils.get_trading_days(end=calc_date, ndays=40, ascending=False)
        # 2.取得个股及指数过去__days+1个交易日每个交易日的开盘价、中午收盘价和当天收盘价
        #   开盘价为09:31分钟线的开盘价，中午收盘价为11:30分钟线的收盘价，当天收盘价为15:00分钟线的收盘价
        #   返回的数据格式为DataFrame，columns=['date','open','mid_close','close']，按日期升序排列
        secu_mkt_data = DataFrame()
        index_mkt_data = DataFrame()
        mkt_data_header = ['date', 'open', 'mid_close', 'close']
        k = 0
        for trading_day in trading_days:
            df_1min_data = Utils.get_min_mkt(Utils.code_to_symbol(code), trading_day, fq=True)
            if df_1min_data is not None:
                str_date = Utils.datetimelike_to_str(trading_day)
                fopen = df_1min_data[df_1min_data.datetime == '%s 09:31:00' % str_date].iloc[0].open
                fmid_close = df_1min_data[df_1min_data.datetime == '%s 11:30:00' % str_date].iloc[0].close
                fclose = df_1min_data[df_1min_data.datetime == '%s 15:00:00' % str_date].iloc[0].close
                secu_mkt_data = secu_mkt_data.append(
                    Series([str_date, fopen, fmid_close, fclose], index=mkt_data_header), ignore_index=True)

                df_1min_data = Utils.get_min_mkt(factor_ct.APM_CT.index_code, trading_day, index=True, fq=True)
                fopen = df_1min_data[df_1min_data.datetime == '%s 09:31:00' % str_date].iloc[0].open
                fmid_close = df_1min_data[df_1min_data.datetime == '%s 11:30:00' % str_date].iloc[0].close
                fclose = df_1min_data[df_1min_data.datetime == '%s 15:00:00' % str_date].iloc[0].close
                index_mkt_data = index_mkt_data.append(
                    Series([str_date, fopen, fmid_close, fclose], index=mkt_data_header), ignore_index=True)
                k += 1
                if k > cls.__days:
                    break
        if k <= cls.__days:
            return None
        secu_mkt_data = secu_mkt_data.sort_values(by='date')
        secu_mkt_data = secu_mkt_data.reset_index(drop=True)
        index_mkt_data = index_mkt_data.sort_values(by='date')
        index_mkt_data = index_mkt_data.reset_index(drop=True)
        #  3.计算个股及指数的上午收益率数组r_t^{am},R_t^{am}和下午收益率数组r_t^{pm},R_t^{pm}，并拼接为一个数组
        #    拼接后的收益率数组，上半部分为r_t^{am} or R_t^{am}，下半部分为r_t^{pm} or R_t^{pm}
        r_am_array = np.zeros((cls.__days, 1))
        r_pm_array = np.zeros((cls.__days, 1))
        for ind in secu_mkt_data.index[1:]:
            r_am_array[ind-1, 0] = secu_mkt_data.loc[ind, 'mid_close'] / secu_mkt_data.loc[ind-1, 'close'] - 1.0
            r_pm_array[ind-1, 0] = secu_mkt_data.loc[ind, 'close'] / secu_mkt_data.loc[ind, 'mid_close'] - 1.0
        r_apm_array = np.concatenate((r_am_array, r_pm_array), axis=0)

        R_am_array = np.zeros((cls.__days, 1))
        R_pm_array = np.zeros((cls.__days, 1))
        for ind in index_mkt_data.index[1:]:
            R_am_array[ind-1, 0] = index_mkt_data.loc[ind, 'mid_close'] / index_mkt_data.loc[ind-1, 'close'] - 1.0
            R_pm_array[ind-1, 0] = index_mkt_data.loc[ind, 'close'] / index_mkt_data.loc[ind, 'mid_close'] - 1.0
        R_apm_array = np.concatenate((R_am_array, R_pm_array), axis=0)
        # 4.个股收益率数组相对于指数收益率进行线性回归
        #   将指数收益率数组添加常数项
        R_apm_array = sm.add_constant(R_apm_array)
        #   线性回归：r_i = \alpha + \beta * R_i + \epsilon_i
        stat_model = sm.OLS(r_apm_array, R_apm_array)
        stat_result = stat_model.fit()
        resid_array = stat_result.resid.reshape((cls.__days*2, 1))   # 回归残差数组
        # 5.计算stat统计量
        #   以上得到的__days*2个残差\epsilon_i中，属于上午的记为\epsilon_i^{am},属于下午的记为\epsilong_i^{pm}，计算每日上午与
        #   下午残差的差值：$\sigma_t = \spsilon_i^{am} - \epsilon_i^{pm}$，为了衡量上午与下午残差的差异程度，设计统计量：
        #   $stat = \frac{\mu(\sigma_t)}{\delta(\sigma_t)\sqrt(N)}$，其中\mu为均值，\sigma为标准差,N=__days，总的来说
        #   统计量stat反映了剔除市场影响后股价行为上午与下午的差异程度。stat数值大（小）于0越多，则股票在上午的表现越好（差）于下午。
        delta_array = resid_array[:cls.__days] - resid_array[cls.__days:]   # 上午与 下午的残差差值
        delta_avg = np.mean(delta_array)    # 残差差值的均值
        delta_std = np.std(delta_array)     # 残差差值的标准差
        stat = delta_avg / delta_std / np.sqrt(cls.__days)
        return stat

    @classmethod
    def _calc_factor_loading_proc(cls, code, calc_date, q):
        logging.info('[%s] Calc APM of %s.' % (calc_date.strftime('%Y-%m-%d'), code))
        stat = cls._calc_factor_loading(code, calc_date)
        ret20 = Utils.calc_interval_ret(code, end=calc_date, ndays=20)
        if stat is not None and ret20 is not None:
            q.put((Utils.code_to_symbol(code), stat, ret20))

    @classmethod
    def calc_factor_loading(cls, start_date, end_date=None, month_end = True, save=False):
        """
        计算指定日期的样本个股的因子载荷，并保存至因子数据库
        Parameters
        --------
        :param start_date: datetime-like, str
            开始日期
        :param end_date: datetime-like, str，默认None
            结束日期，如果为None，则只计算start_date日期的因子载荷
        :param month_end: bool，默认True
            只计算月末时点的因子载荷，该参数只在end_date不为None时有效，并且不论end_date是否为None，都会计算第一天的因子载荷
        :param save: 是否保存至因子数据库，默认为False
        :return: 因子载荷，DataFrame
        --------
            因子载荷,DataFrame
            0: ID, 证券ID，为索引
            1: factorvalue, 因子载荷
            如果end_date=None，返回start_date对应的因子载荷数据
            如果end_date!=None，返回最后一天的对应的因子载荷数据
            如果没有计算数据，返回None
        """
        # 1.取得交易日序列及股票基本信息表
        start_date = Utils.to_date(start_date)
        if end_date is not None:
            end_date = Utils.to_date(end_date)
            trading_days_series = Utils.get_trading_days(start=start_date, end=end_date)
        else:
            trading_days_series = Utils.get_trading_days(end=start_date, ndays=1)
        all_stock_basics = CDataHandler.DataApi.get_secu_basics()
        # 2.遍历交易日序列，计算APM因子载荷
        dict_apm = None
        for calc_date in trading_days_series:
            dict_apm = {'ID': [], 'factorvalue': []}
            if month_end and (not Utils.is_month_end(calc_date)):
                continue
            # 2.1.遍历个股，计算个股APM.stat统计量，过去20日收益率，分别放进stat_lst,ret20_lst列表中
            s = (calc_date - datetime.timedelta(days=90)).strftime('%Y%m%d')
            stock_basics = all_stock_basics[all_stock_basics.list_date < s]
            stat_lst = []
            ret20_lst = []
            symbol_lst = []

            # for _, stock_info in stock_basics.iterrows():
            #     stat_i = cls._calc_factor_loading(stock_info.symbol, calc_date)
            #     ret20_i = Utils.calc_interval_ret(stock_info.symbol, end=calc_date, ndays=20)
            #     if stat_i is not None and ret20_i is not None:
            #         stat_lst.append(stat_i)
            #         ret20_lst.append(ret20_i)
            #         symbol_lst.append(Utils.code_to_symbol(stock_info.symbol))
            #         logging.info('APM of %s = %f' % (stock_info.symbol, stat_i))

            # 采用多进程平行计算
            q = Manager().Queue()
            p = Pool(4)     # 最多同时开启4个进程
            for _, stock_info in stock_basics.iterrows():
                p.apply_async(cls._calc_factor_loading_proc, args=(stock_info.symbol, calc_date, q,))
            p.close()
            p.join()
            while not q.empty():
                apm_value = q.get(True)
                symbol_lst.append(apm_value[0])
                stat_lst.append(apm_value[1])
                ret20_lst.append(apm_value[2])
            assert len(stat_lst) == len(ret20_lst)
            assert len(stat_lst) == len(symbol_lst)

            # 2.2.将统计量stat对动量因子ret20j进行截面回归：stat_j = \beta * Ret20_j + \epsilon_j
            #     残差向量即为对应个股的APM因子
            stat_arr = np.array(stat_lst).reshape((len(stat_lst), 1))
            ret20_arr = np.array(ret20_lst).reshape((len(ret20_lst), 1))
            ret20_arr = sm.add_constant(ret20_arr)
            apm_model = sm.OLS(stat_arr, ret20_arr)
            apm_result = apm_model.fit()
            apm_lst = list(np.around(apm_result.resid, 6))  # amp因子载荷精确到6位小数
            assert len(apm_lst) == len(symbol_lst)
            # 2.3.构造APM因子字典，并持久化
            dict_apm = {'ID': symbol_lst, 'factorvalue': apm_lst}
            if save:
                Utils.factor_loading_persistent(cls._db_file, calc_date.strftime('%Y%m%d'), dict_apm)
        return dict_apm


def apm_backtest(start, end):
    """
    APM因子的历史回测
    Parameters:
    --------
    :param start: datetime-like, str
        回测开始日期，格式：YYYY-MM-DD，开始日期应该为月初的前一个交易日，即月末交易日
    :param end: datetime-like, str
        回测结束日期，格式：YY-MM-DD
    :return:
    """


if __name__ == '__main__':
    # pass
    APM.calc_factor_loading('2012-12-31', month_end=True, save=True)