"""
Printer class holding all printer components

Author: Mathieu Monney
email: zittix(at)xwaves(dot)net
Website: http://www.xwaves.net
License: GNU GPL v3: http://www.gnu.org/copyleft/gpl.html

 Redeem is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 Redeem is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with Redeem.  If not, see <http://www.gnu.org/licenses/>.
"""

from Path import Path
import numpy as np
import logging
from Delta import Delta
from PruInterface import PruInterface
import os
import json

class Printer:
    AXES = "XYZEHABC"
    axes_zipped = ["X", "Y", "Z", "E", "H", "A", "B", "C"]
    MAX_AXES = 8
    NUM_AXES = 5

    AXIS_CONFIG_XY      = 0
    AXIS_CONFIG_H_BELT  = 1
    AXIS_CONFIG_CORE_XY = 2
    AXIS_CONFIG_DELTA   = 3

    def __init__(self):
        self.config_location = None
        self.steppers    = {}
        self.heaters     = {}
        self.thermistors = {}
        self.mosfets     = {}
        self.end_stops   = {}
        self.fans        = []
        self.cold_ends   = []
        self.coolers     = []
        self.comms       = {}  # Communication channels
        self.path_planner   = None
        self.factor         = 1.0
        self.extrude_factor = 1.0
        self.movement       = Path.ABSOLUTE
        self.axis_config    = self.AXIS_CONFIG_XY
        self.feed_rate      = 0.5
        self.accel          = 0.5
        self.current_tool   = "E"
        # For movement commands, whether the E axis refers to the active
        # tool (more common with other firmwares), or only the actual E axis
        self.e_axis_active = True
        self.move_cache_size        = 128
        self.print_move_buffer_wait = 250
        self.min_buffered_move_time = 100
        self.max_buffered_move_time = 1000

        self.max_length = 0.001

        self.probe_points  = []
        self.probe_heights = [0, 0, 0]
        self.probe_type = 0 # Servo

        # Max number of axes.
        self.num_axes = 8

        self.max_speeds             = np.ones(self.num_axes)
        self.min_speeds             = np.ones(self.num_axes)*0.01
        self.jerks                  = np.ones(self.num_axes)*0.01
        self.acceleration           = [0.3]*self.num_axes
        self.home_speed             = np.ones(self.num_axes)
        self.home_backoff_speed     = np.ones(self.num_axes)
        self.home_backoff_offset    = np.zeros(self.num_axes)
        self.steps_pr_meter         = np.ones(self.num_axes)
        self.backlash_compensation  = np.zeros(self.num_axes)
        self.backlash_state         = np.zeros(self.num_axes)
        self.soft_min               = -np.ones(self.num_axes)*1000.0
        self.soft_max               = np.ones(self.num_axes)*1000.0
        self.slaves                 = {key: "" for key in self.AXES[:self.num_axes]}

        # bed compensation
        self.matrix_bed_comp = np.eye((3))

        # By default, do not check for slaves
        self.has_slaves = False

        return

    def add_slave(self, master, slave):
        ''' Make an axis copy the movement of another.
        the slave will get the same position as the axis'''
        self.slaves[master] = slave
        self.has_slaves = True
        return

    def check_values(self):
        """
        make sure that values are valid
        """

        # check min speed
        for axis in self.steppers:
            stepper = self.steppers[axis]
            if stepper.in_use:
                idx = Printer.axis_to_index(axis)
                steps_per_second = self.min_speeds[idx]*self.steps_pr_meter[idx]
                logging.debug("Axis {0} min steps/s = {1}".format(axis, steps_per_second))
                if steps_per_second < 1:
                    err = "minimum speed of axis {0} is too low. Increase min_speed_{0}, microstepping_{0}, or adjust steps_pr_mm_{0}".format(axis.lower())
                    logging.warning(err)
                    raise RuntimeError(err)


        return

    def ensure_steppers_enabled(self):
        """
        This method is called for every move, so it should be fast/cached.
        """
        # Reset Stepper watchdog
        self.swd.reset()
        # Enabe steppers
        for name, stepper in self.steppers.iteritems():
            if stepper.in_use and not stepper.enabled:
                # Stepper should be enabled, but is not.
                stepper.set_enabled(True)  # Force update

    def reply(self, gcode):
        """ Send a reply through the proper channel """
        if gcode.get_answer() is not None:
            self.send_message(gcode.prot, gcode.get_answer())

    def send_message(self, prot, msg):
        """ Send a message back to host """
        if "\n" in msg:
            for m in msg.split("\n"):
                if len(m) > 0:
                    self.comms[prot].send_message(m)
        else:
            self.comms[prot].send_message(msg)

    def homing(self, is_homing):
        """
        if the printer is homing the endstops may need to be updated to
        allow for endstops that are only active during the homing procedure
        """

        homing_only_endstops = self.config.get('Endstops','homing_only_endstops')
        if homing_only_endstops:
            for es in self.end_stops.items():
                if es[0] in homing_only_endstops:
                    es[1].active = is_homing

        self.set_active_endstops()

        return

    def set_active_endstops(self):
        """
        go through the list of endstops and load their active status into the PRU
        """

        # generate a binary representation of the active status
        active = 0
        for i, es in enumerate(["X1","Y1","Z1","X2","Y2","Z2"]):
            if self.end_stops[es].active:
                active += 1 << i

        #logging.debug("endstop active mask = " + bin(active))

        # write to shared memory
        PruInterface.set_active_endstops(active)
        return


    def save_settings(self, filename):
        logging.debug("save_settings: setting stepper parameters")
        for name, stepper in self.steppers.iteritems():
            self.config.set('Steppers', 'in_use_' + name, str(stepper.in_use))
            self.config.set('Steppers', 'direction_' + name, str(stepper.direction))
            self.config.set('Endstops', 'has_' + name, str(stepper.has_endstop))
            self.config.set('Steppers', 'current_' + name, str(stepper.current_value))
            self.config.set('Steppers', 'steps_pr_mm_' + name, str(stepper.steps_pr_mm))
            self.config.set('Steppers', 'microstepping_' + name, str(stepper.microstepping))
            self.config.set('Steppers', 'slow_decay_' + name, str(stepper.decay))
            self.config.set('Steppers', 'slave_' + name, str(self.slaves[name]))

        logging.debug("save_settings: setting heater parameters")
        for name, heater in self.heaters.iteritems():
            self.config.set('Heaters', 'pid_Kp_'+name, str(heater.Kp))
            self.config.set('Heaters', 'pid_Ti_'+name, str(heater.Ti))
            self.config.set('Heaters', 'pid_Td_'+name, str(heater.Td))

        logging.debug("save_settings: saving bed compensation matrix")
        # Bed compensation
        self.save_bed_compensation_matrix()

        # Offsets
        logging.debug("save_settings: setting offsets")
        for axis, offset in self.path_planner.center_offset.iteritems():
            if self.config.has_option("Geometry", "offset_{}".format(axis)):
                self.config.set('Geometry', "offset_{}".format(axis), str(offset))

        # Save Delta shit
        logging.debug("save_settings: setting delta shit")
        opts = ["Hez", "L", "r", "Ae", "Be", "Ce", "A_radial", "B_radial", "C_radial", "A_tangential", "B_tangential", "C_tangential" ]
        for opt in opts:
            self.config.set('Delta', opt, str(Delta.__dict__[opt]))

        logging.debug("save_settings: saving config to file")
        self.config.save(filename)
        logging.debug("save_settings: done")

    def load_bed_compensation_matrix(self):
        try:
            mat = self.config.get('Geometry', 'bed_compensation_matrix')
            mat = np.array(json.loads(mat))
        except:
            mat = np.eye(3)
        return mat

    def save_bed_compensation_matrix(self):
        mat = json.dumps(self.matrix_bed_comp.tolist())
        # Only update if they are different
        if mat != self.config.get('Geometry', 'bed_compensation_matrix'):
            self.config.set('Geometry', 'bed_compensation_matrix', mat)

    def movement_axis(self, axis):
        if self.e_axis_active and axis == "E":
            return self.current_tool

        return axis

    @staticmethod
    def axis_to_index(axis):
        return Printer.AXES.index(axis)

    @staticmethod
    def index_to_axis(index):
        return Printer.AXES[index]
