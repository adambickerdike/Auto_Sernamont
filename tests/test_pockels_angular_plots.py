import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import csv
import math
from pathlib import Path
import tempfile
import unittest

from pockels_angular_plots import save_pixel_angular_products
from pockels_measurement_analysis import fit_angular_response


class AngularPlotProductTests(unittest.TestCase):
    def test_products_use_normalized_retardation_and_mark_selected_hwp(self):
        fits = []
        for index, theta in enumerate(range(0, 180, 15)):
            phase = math.radians(2.0 * theta)
            response = complex(
                8e-6 + 24e-6 * math.cos(phase),
                4e-6 + 10e-6 * math.sin(phase),
            )
            fits.append({
                "hwp_deg": 75.0 - 2.5 * index,
                "theta_i_deg": float(theta),
                "rotation_slope_x_rad_per_Vrms": response.real,
                "rotation_slope_y_rad_per_Vrms": response.imag,
                "rotation_slope_mag_rad_per_Vrms": abs(response),
                "geometry_certified": True,
            })
        angular_fit = fit_angular_response(fits)
        summary = {
            "pixel": 7,
            "angular_response_fit": angular_fit,
            "normalized_peak_certificate": {
                "status": "certified",
                "hwp_deg": angular_fit["selected_measured_hwp_deg"],
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            products = save_pixel_angular_products(output_dir, summary)
            product_names = {path.name for path in products}
            self.assertEqual(product_names, {
                "fast_map_angular_response.csv",
                "fast_map_angular_polar.png",
                "fast_map_angular_polar.pdf",
                "fast_map_angular_diagnostics.png",
                "fast_map_angular_diagnostics.pdf",
            })
            for path in products:
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 100)

            with (output_dir / "fast_map_angular_response.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), len(fits))
        for row in rows:
            self.assertAlmostEqual(
                float(row["retardation_mag_rad_per_Vrms"]),
                2.0 * float(row["rotation_mag_rad_per_Vrms"]),
                places=14,
            )
        self.assertEqual(
            sum(int(row["selected_for_hysteresis"]) for row in rows),
            1,
        )


if __name__ == "__main__":
    unittest.main()
