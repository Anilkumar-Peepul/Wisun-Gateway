# wisun_gateway/logger.py
import csv
import logging
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from config import COMBINED_LOG_FILE, LOG_DIR

class PayloadLogger:
    def __init__(self):
        self.combined_file = COMBINED_LOG_FILE
        self._setup_logger()

        # Anomaly Thresholds
        self.pfa = 400
        self.lva = 410
        self.lvf = 405
        self.hva = 450
        self.hvf = 455
        self.vif = 20
        self.via = 15
        self.m_f_dr = 0.2
        self.m_f_ol = 0.3
        self.m_f_ci = 0.2
        self.m_a_dr = 0.3
        self.m_a_ol = 0.4
        self.m_a_ci = 0.15

    def _setup_logger(self):
        self.logger = logging.getLogger("wisun_gateway")
        self.logger.setLevel(logging.INFO)

        handler = RotatingFileHandler(
            LOG_DIR / "gateway.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=5
        )
        formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def max_diff(self, arr):
        return max(arr) - min(arr) if arr else 0

    def avg_currents(self, c):
        return sum(c) / len(c) if c else 0

    def check_voltage_anomalies(self, ll_v):
        return {
            "In_Ph_Fl_Amly": any(v < self.pfa for v in ll_v),
            "Lo_V_Fl_Amly": any(v < self.lvf for v in ll_v),
            "Hi_V_Fl_Amly": any(v > self.hvf for v in ll_v),
            "V_Imb_Fl_Amly": self.max_diff(ll_v) > self.vif,
            "Lo_V_Al_Amly": any(v < self.lva for v in ll_v),
            "Hi_V_Al_Amly": any(v > self.hva for v in ll_v),
            "V_Imb_Al_Amly": self.max_diff(ll_v) > self.via,
        }

    def check_motor_anomalies(self, motor_c, mtr_id):
        avg = self.avg_currents(motor_c)
        return {
            f"Dry_M{mtr_id}_Fl_Amly": avg < self.m_f_dr,
            f"Ov_Ld_M{mtr_id}_Fl_Amly": avg > self.m_f_ol or max(motor_c) > self.m_f_ol,
            f"C_Imb_M{mtr_id}_Fl_Amly": self.max_diff(motor_c) > self.m_f_ci,
            f"Dry_M{mtr_id}_Al_Amly": avg < self.m_a_dr,
            f"Ov_Ld_M{mtr_id}_Al_Amly": avg > self.m_a_ol or max(motor_c) > self.m_a_ol,
            f"C_Imb_M{mtr_id}_Al_Amly": self.max_diff(motor_c) > self.m_a_ci,
        }

    def process(self, payload: dict):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data = {
                "timestamp": timestamp,
                "d_id": payload.get("d_id", ""),
                "p_v": payload.get("p_v", 0),
                "pwr": payload.get("pwr", 0),
                "mode": payload.get("mode", 0),
            }

            ll_v = payload.get("ll_v", [0, 0, 0])
            anomaly_data = self.check_voltage_anomalies(ll_v)

            for motor in payload.get("mtr", []):
                mtr_id = motor.get("mtr_id", 0)
                motor_c = motor.get("amp", [0, 0, 0])
                anomaly_data.update(self.check_motor_anomalies(motor_c, mtr_id))

            # Merge and save
            combined = {**data, **anomaly_data}
            self._save_to_csv(combined)
            self.logger.info(f"Processed data from {data['d_id']}")

        except Exception as e:
            self.logger.error(f"Logger processing error: {e}")

    def _save_to_csv(self, row: dict):
        file_exists = self.combined_file.exists()
        with open(self.combined_file, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
