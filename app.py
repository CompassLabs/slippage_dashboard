import os
from decimal import Decimal

import streamlit as st
from dotenv import load_dotenv

from dojo.agents import DummyAgent
from dojo.common.constants import Chain
from dojo.config import cfg
from dojo.config.config import load_network_cfg
from dojo.environments import UniV3Env
from dojo.environments.uniswapV3 import UniV3Quote, UniV3Trade
from dojo.network.block_date import block_to_datetime

load_dotenv()

cfg.network = load_network_cfg()

RPC_URL = os.environ.get("ETHEREUM_RPC_URL")
st.set_page_config(layout="wide")

@st.cache_data
def get_state(pool, date_range):
    tempenv = UniV3Env(
        Chain.ETHEREUM,
        date_range=date_range,
        agents=[],
        pools=[pool],
        backend_type="forked",
        market_impact="replay",
    )
    obs = tempenv.reset()
    _, tick, _, _, _, _, _ = obs.slot0(pool)
    liquidity = obs.liquidity(pool)
    token0, token1 = obs.tokens()
    price_1_in_0 = obs.price(token=token1, unit=token0, pool=pool)
    fee = obs.pool_fee(pool=pool)
    tick_spacing = obs.tick_spacing(pool)
    cur_lower = (tick // tick_spacing) * tick_spacing
    del tempenv
    return liquidity, price_1_in_0, fee, tick_spacing, cur_lower, tick


POOLS = [
    i for i in cfg.network.deployments["ethereum"]["UniswapV3"].keys() if "/" in i
]  # TODO provide function to get this list easier

st.header("Slippage dashboard")
st.text("The Slippage Dashboard offers detailed insights into slippage within Uniswap pools.")
st.text("Although Dojo primarily focuses on comprehensive backtesting, it's also perfectly capable of handling simpler tasks like analyzing and understanding slippage with ease.")
st.markdown("---")
st.markdown("### Choose pool of interest")
pool = st.selectbox(label="Select pool", options=POOLS, key="POOLS")
st.markdown("---")
if pool:
    start_block = (
        cfg.network.deployments["ethereum"]["UniswapV3"][pool]["start_block"] + 1
    )
    max_block = 20499736
    block = st.slider(
        label="Select block",
        min_value=start_block,
        max_value=max_block,
        value=(max_block + start_block) >> 1,
        step=1,
    )
    token0, token1 = pool.split("/")[0], pool.split("/")[1].split("-")[0]


liquidity, price_1_in_0, fee, tick_spacing, cur_lower, tick = get_state(
    pool,
    date_range=(block_to_datetime(RPC_URL, block), block_to_datetime(RPC_URL, block)),
)
with st.expander(label="Show pool state before any actions.", expanded=False):
    st.markdown(f"**Tick**: {tick}")
    st.markdown(f"**Liquidity**: {liquidity}")
    st.markdown(f"**Price({token1} in {token0})**:     {price_1_in_0}")
    st.markdown(f"**Fee**: {tick}")
    st.markdown(f"**Tick Spacing**: {tick_spacing}")


st.markdown("---")
st.markdown("### Provide additional liquidity")
st.text("You can choose to add more liquidity to see how it impacts slippage. ")
st.text("Please note that single-sided (one-token-only) liquidity provision is not permitted if you provide liquidity in the active tick range.")
do_extra_liquidity = st.toggle(
    label="Provide extra liquidity before trading?", value=True
)
extra_token0, extra_token1 = 0, 0
if do_extra_liquidity:
    values = st.slider(
        "What tick range do you want the extra liquidity in?",
        min_value=cur_lower - 10 * tick_spacing,
        max_value=cur_lower + 10 * tick_spacing,
        value=(cur_lower, cur_lower + tick_spacing),
        step=tick_spacing,
    )
    col0, col1 = st.columns(2)
    with col0:
        extra_token0 = st.number_input(label=f"How much {token0}?", key="extra_token0")
    with col1:
        extra_token1 = st.number_input(label=f"How much {token1}?", key="extra_token1")

st.markdown("---")
st.markdown("### Specify trade")
st.text("In this example we are using the pool to trade an arbitrary amount of one token into another token.")
col2, col3 = st.columns(2)
with col2:
    trade_token = st.selectbox(
        label="Which token do you want to trade?", options=(token0, token1)
    )
with col3:
    trade_amount = st.number_input(label=f"How much {trade_token}?")
st.markdown("---")
go_button = st.button("Simulate trade")


if go_button:
    with st.spinner("Simulating..."):
        lp_agent = DummyAgent(
            initial_portfolio={
                token0: Decimal(extra_token0),
                token1: Decimal(extra_token1),
                "ETH": Decimal(0.2),
            },  # Using this agent to massively increase pool liquidity
            name="LPAgent",
        )
        initial_portfolio = initial_portfolio = {
            "ETH": Decimal(0.2),  # just a bit of ETH to cover fees
            token0: Decimal(trade_amount) if trade_token == token0 else Decimal(0),
            token1: Decimal(trade_amount) if trade_token == token1 else Decimal(0),
        }
        trader_agent = DummyAgent(
            initial_portfolio=initial_portfolio,
            name="TraderAgent",
        )
        env = UniV3Env(
            Chain.ETHEREUM,
            date_range=(
                block_to_datetime(RPC_URL, block),
                block_to_datetime(RPC_URL, block),
            ),
            agents=[trader_agent, lp_agent],
            pools=[pool],
            backend_type="forked",
            market_impact="replay",
        )
        obs = env.reset()

        if do_extra_liquidity:

            env.step(
                actions=[
                    UniV3Quote(
                        agent=lp_agent,
                        pool=pool,
                        quantities=[
                            Decimal(extra_token0),
                            Decimal(extra_token1),
                        ],  # Change these parameters to add liqudidity
                        tick_range=values,
                    )
                ]
            )
            liquidity_post_lp = obs.liquidity(pool)
            with st.expander(
                label="Show pool state after LP provision.", expanded=False
            ):
                st.markdown(f"**Tick**: {tick}")
                st.markdown(f"**Liquidity**: {liquidity_post_lp}")

        env.step(
            actions=[
                UniV3Trade(
                    agent=trader_agent,
                    pool=pool,
                    quantities=[
                        Decimal(trade_amount) if trade_token == token0 else Decimal(0),
                        Decimal(trade_amount) if trade_token == token1 else Decimal(0),
                    ],
                )
            ]
        )
        without_slippage = trader_agent.portfolio()[token1] * price_1_in_0 * (1 - fee)
        with_slippage = trader_agent.portfolio()[token0]
        slippage = without_slippage - with_slippage

        _, tick_post_trade, _, _, _, _, _ = obs.slot0(pool)
        liquidity_post_trade = obs.liquidity(pool)
        price_1_in_0_post_trade = obs.price(token=token1, unit=token0, pool=pool)

        trader_agent.portfolio()
        post_portfolio = trader_agent.portfolio()
        effective_price = (post_portfolio[token0] - initial_portfolio[token0]) / (
            initial_portfolio[token1] - post_portfolio[token1]
        )

        post_trade_tick_range = obs.active_tick_range(pool)
        with st.expander(label="Show pool state after trade.", expanded=False):
            st.markdown(f"**Tick before**: {tick}")
            st.markdown(f"**Tick before**: {tick_post_trade}")
            st.markdown(f"**Tick liquidity before**: {liquidity}")
            st.markdown(f"**Tick liquidity after**: {liquidity_post_trade}")
            st.markdown(f"**Price({token1} in {token0}) before**:     {price_1_in_0}")
            st.markdown(
                f"**Price({token1} in {token0}) after**:     {price_1_in_0_post_trade}"
            )

        st.text(
            f"slippage = {abs(round(float(1-effective_price/price_1_in_0),4)*100)} %"
        )
