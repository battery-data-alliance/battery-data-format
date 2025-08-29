import pandas as pd
from bdf.visualize import line_plot

def test_line_plot_returns_figure(tmp_path):
    df = pd.DataFrame({
        "Test Time / s": [0, 1, 2],
        "Voltage / V": [4.20, 4.18, 4.16],
        "Current / A": [0.5, 0.5, 0.4],
    })
    out = tmp_path / "plot.png"
    fig = line_plot(df, xdata="Test Time / s", ydata="Voltage / V", save=str(out))
    assert out.exists() and out.stat().st_size > 0
    assert fig is not None
