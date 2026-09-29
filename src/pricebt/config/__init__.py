"""DisplayOptions and the module default `display_options` (ported from gs_quant.config.options,
Apache-2.0; see NOTICE). `PortfolioRiskResult.to_frame(display_options=...)` reads `show_na`;
None falls back to this module's `display_options`, read at call time."""


class DisplayOptions:
    """
    config options
    """

    def __init__(self, show_na: bool = False):
        self.__show_na = show_na

    @property
    def show_na(self):
        return self.__show_na

    @show_na.setter
    def show_na(self, show_na):
        self.__show_na = show_na


display_options = DisplayOptions()
