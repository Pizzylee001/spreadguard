"""Quick comparative run: naive vs pull vs stepback, 5 seeds, identical market paths."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from spreadguard.config import SpreadGuardConfig, QuoteMode
from backtest.simulator import run_backtest

def main():
    header = f"{'mode':<10} {'vol':>9} {'pnl':>8} {'fills':>6} {'maxdd':>7} {'fires/hr':>9}  states"
    print(header)
    for mode in [QuoteMode.NAIVE, QuoteMode.PULL, QuoteMode.STEP_BACK]:
        rs = [run_backtest(SpreadGuardConfig(mode=mode), num_ticks=2400, seed=s) for s in (42, 101, 888, 777, 555)]
        n = len(rs)
        vol = sum(r.total_volume_usdt for r in rs) / n
        pnl = sum(r.net_pnl_usdt for r in rs) / n
        fills = sum(r.fill_count for r in rs) / n
        dd = max(r.max_drawdown_usdt for r in rs)
        fires = rs[0].gate_fire_rate_per_hr
        st = {}
        for r in rs:
            for k, v in r.states_fired.items():
                st[k] = st.get(k, 0) + v
        line = f"{mode.value:<10} {vol:9.0f} {pnl:+8.2f} {fills:6.0f} {dd:7.2f} {fires:9.0f}  {st}"
        print(line)

if __name__ == "__main__":
    main()
