"""
elliptec_serial.py
Direct serial control for Elliptec rotators - FAST and supports multiple simultaneous connections
Based on Thorlabs Elliptec protocol
"""

import serial
import time

def from_twos_complement(n, bits=32):
    """Convert from two's complement representation."""
    if n < (1 << (bits-1)): 
        return n
    return n - (1 << bits)

def to_twos_complement(n, bits=32):
    """Convert to two's complement representation."""
    return (1 << bits) + n if n < 0 else n

# Encoder counts per revolution (398 pulses/deg * 360 deg)
COUNTS_PER_REVOLUTION = 143360

# Status response codes
RESPONSES = [
    'ok',
    'communication timeout',
    'mechanical timeout',
    'command error',
    'value out of range',
    'module isolated',
    'module out of isolation',
    'initialization error',
    'thermal error',
    'busy',
    'sensor error',
    'motor error',
    'out of range',
    'overcurrent',
]


class ElliptecRotator:
    """
    Direct serial control for Thorlabs Elliptec rotators (ELL14/ELL18).
    Much faster than DLL and supports true parallel operation.
    """
    
    def __init__(
        self,
        port='COM4',
        address=0,
        baudrate=9600,
        timeout=0.1,
        verbose=True,
        settle_time=1.5,
    ):
        """
        Initialize rotator connection.
        
        Args:
            port: Serial port (e.g., 'COM4', '/dev/ttyUSB0')
            address: Device address on the bus (0-F, usually 0 for single device)
            baudrate: Serial baudrate (default 9600)
            timeout: Serial read timeout in seconds
            verbose: Print status messages
            settle_time: Default settle time after moves (seconds)
        """
        self.port = port
        self.address = address
        self.verbose = verbose
        self.default_settle = settle_time
        
        self._conn = serial.Serial(
            port, 
            baudrate=baudrate, 
            stopbits=1, 
            parity='N', 
            timeout=timeout,
            write_timeout=2.0,
        )
        self._offset = 0
        
        # Clear any stale data in buffer
        try:
            self._conn.reset_input_buffer()
            self._conn.reset_output_buffer()
        except BaseException:
            self._conn.close()
            raise
        
        if self.verbose:
            print(f"Connected to {port} (Address {address})")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc, tb):
        try:
            self.return_home()
        finally:
            self.close()
    
    def send(self, command, data=b''):
        """Send command to device without waiting for response."""
        packet = (
            str(self.address).encode('utf-8')
            + command.encode('utf-8')
            + data.hex().upper().encode('utf-8')
            + b'\n'
        )
        self._conn.write(packet)
        time.sleep(0.02)  # Small delay to ensure command is sent
    
    def query(self, command, data=b'', timeout=5.0, max_retries=2):
        """
        Send command and wait for response with automatic retry.
        
        Args:
            command: Command string (e.g., 'gs', 'gp', 'mr')
            data: Command data as bytes
            timeout: Timeout in seconds for response
            max_retries: Number of retries on timeout/error
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Clear buffer before sending
                self._conn.reset_input_buffer()
                
                self.send(command, data=data)
                response = b''
                start_time = time.time()
                
                while True:
                    chunk = self._conn.read(8192)
                    response += chunk
                    
                    if response.endswith(b'\r\n'): 
                        break
                    
                    # Check timeout
                    if time.time() - start_time > timeout:
                        raise TimeoutError(f"No response from device at {self.port}")
                    
                    time.sleep(0.01)
                
                # Parse response: address(1) + command(2) + data(8) + \r\n
                if len(response) < 3:
                    raise ValueError(f"Invalid response: {response}")
                
                header = response[:3]
                data_hex = response[3:-2]
                
                # Verify address matches
                if chr(header[0]) != str(self.address):
                    raise ValueError(f"Address mismatch: expected {self.address}, got {chr(header[0])}")
                
                response_type = header[1:].decode()
                response_data = int(data_hex.decode(), 16) if data_hex else 0
                
                return response_type, response_data
                
            except (TimeoutError, ValueError, serial.SerialException) as e:
                last_error = e
                if attempt < max_retries - 1:
                    if self.verbose:
                        print(f"[{self.port}] Query error, retrying... ({attempt + 1}/{max_retries})")
                    time.sleep(0.15)
                    self._conn.reset_input_buffer()
                    continue
                    
        # All retries exhausted
        raise last_error
    
    @property
    def status(self):
        """Get current device status."""
        header, response = self.query('gs')
        if header != 'GS':
            raise ValueError(f"Unexpected response type: {header}")
        return RESPONSES[response] if response < len(RESPONSES) else f"Unknown status {response}"
    
    @property
    def _position(self):
        """Get raw position in encoder counts."""
        header, response = self.query('gp')
        if header != 'PO':
            raise ValueError(f"Unexpected response type: {header}")
        return from_twos_complement(response)

    def get_raw_position_counts(self):
        """Get the un-offset encoder position in raw device counts."""
        return self._position

    def get_raw_angle(self):
        """Get the un-offset device angle in degrees (0-360 range)."""
        position = self.get_raw_position_counts()
        return (-360 * position / COUNTS_PER_REVOLUTION) % 360
    
    def home(self, direction=0, settle_s=3.5, max_retries=3):
        """
        Return stage to home position.
        
        Args:
            direction: 0 for CW, 1 for CCW
            settle_s: Time to wait after homing
            max_retries: Maximum retry attempts
        """
        if self.verbose:
            print(f"[{self.port}] Homing...")
        
        last_error = None
        for attempt in range(max_retries):
            try:
                # Send home command with direction
                self.send('ho', bytes([direction]))
                time.sleep(settle_s)
                return  # Success
            except (TimeoutError, ValueError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    if self.verbose:
                        print(f"[{self.port}] Homing error, retrying... ({attempt + 1}/{max_retries})")
                    time.sleep(0.5)
                    continue
        
        raise ValueError(f"Homing failed after {max_retries} attempts: {last_error}")
    
    def return_home(self, settle_s=3.5):
        """Return to home position and reset to 0°."""
        if self.verbose:
            print(f"[{self.port}] Returning to home (0°)...")
        self.home(direction=0, settle_s=settle_s)
        self.tare()
    
    def tare(self):
        """Mark current position as 0° in software."""
        self._offset = -self._position
    
    @property
    def angle_unwrapped(self):
        """Get angle counting full rotations (can exceed 360°)."""
        return -360 * (self._position + self._offset) / COUNTS_PER_REVOLUTION
    
    def get_angle(self):
        """Get current angle in degrees (0-360 range)."""
        try:
            # Query position using 'gp' command
            header, response = self.query('gp')
            if header != 'PO':
                raise ValueError(f"Unexpected response type: {header}")
            
            # Convert raw position to angle
            position = from_twos_complement(response)
            angle_unwrapped = -360 * (position + self._offset) / COUNTS_PER_REVOLUTION
            return angle_unwrapped % 360
            
        except Exception as e:
            if self.verbose:
                print(f"[{self.port}] Warning: Could not read angle - {e}")
            return None
    
    def set_angle(self, degrees, settle_s=None, read_back=True):
        """
        Move to absolute angle position.

        On sensor/limit/range faults, auto-homes to clear the fault then
        retries the move from the new position.

        Args:
            degrees: Target angle in degrees (0-360)
            settle_s: Time to wait after move for settling (uses default if None)
            read_back: If True, read and return actual angle after move
        """
        if settle_s is None:
            settle_s = self.default_settle

        def _compute_delta():
            current = self.get_angle()
            if current is not None:
                d = degrees - current
                if d > 180: d -= 360
                if d < -180: d += 360
                return d
            return degrees

        delta = _compute_delta()

        if self.verbose:
            print(f"[{self.port}] Moving to {degrees:.4f}°", end='')

        try:
            self.move_by(delta)
        except (ValueError, Exception) as e:
            emsg = str(e).lower()
            if 'sensor' in emsg or 'limit' in emsg or 'range' in emsg:
                if self.verbose:
                    print(f" -> fault ({e}), homing to recover...", end='')
                try:
                    self.home(direction=0, settle_s=2.5)
                    self.tare()
                except Exception:
                    pass
                # Recompute delta from new (post-home) position and retry
                delta = _compute_delta()
                self.move_by(delta)
            else:
                raise

        if settle_s:
            time.sleep(settle_s)

        # Read back actual position
        if read_back:
            actual_angle = self.get_angle()
            if actual_angle is not None:
                if self.verbose:
                    print(f" -> Actual: {actual_angle:.4f}°")
                return actual_angle
            else:
                if self.verbose:
                    print(" -> (readback failed)")
                return degrees
        else:
            if self.verbose:
                print()
            return degrees
    
    def shift_angle(self, degrees, settle_s=None):
        """
        Move by relative angle.
        
        Args:
            degrees: Relative angle to move (positive = CCW)
            settle_s: Time to wait after move
        """
        if settle_s is None:
            settle_s = self.default_settle
            
        if self.verbose:
            sign = '+' if degrees >= 0 else ''
            print(f"[{self.port}] Moving {sign}{degrees:.1f}°")
        
        self.move_by(degrees)
        if settle_s:
            time.sleep(settle_s)
        return degrees
    
    def move_by(self, degrees, max_retries=3):
        """
        Execute relative move with automatic retry on errors.
        
        Args:
            degrees: Angle to move in degrees (positive = CCW)
            max_retries: Maximum number of retry attempts on error
        """
        # Convert degrees to encoder counts (negative because protocol uses CW positive)
        delta = -round(degrees * COUNTS_PER_REVOLUTION / 360)
        data = to_twos_complement(delta).to_bytes(4, 'big')
        
        last_error = None
        for attempt in range(max_retries):
            try:
                header, response = self.query('mr', data=data)
                
                if header not in ['GS', 'PO']:
                    raise ValueError(f"Unexpected response: {header}")
                
                # Check if move was successful
                if header == 'GS':
                    if response == 0:  # 'ok' status
                        return  # Success!
                    else:
                        error_msg = RESPONSES[response] if response < len(RESPONSES) else f"Unknown error {response}"
                        last_error = error_msg
                        
                        if self.verbose and attempt < max_retries - 1:
                            print(f"[{self.port}] Warning: {error_msg}, retrying ({attempt + 1}/{max_retries})...")
                        
                        # Wait before retry
                        time.sleep(0.3)
                        continue
                
                # If header is 'PO', move completed successfully
                return
                
            except (TimeoutError, ValueError) as e:
                last_error = str(e)
                if self.verbose and attempt < max_retries - 1:
                    print(f"[{self.port}] Communication error, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(0.3)
                continue
        
        # All retries failed
        raise ValueError(f"Move failed after {max_retries} attempts. Last error: {last_error}")
    
    def set_velocity(self, percent):
        """Set rotator velocity as a percentage of maximum (1-100).

        Lower velocities reduce inertial overshoot and make the mechanical
        motion more observable / less jarring, at the cost of a
        proportionally longer physical move time. Thorlabs recommends
        values >=25% for reliable operation; below that, the motor may
        stall on reversal.

        Default out of reset is 100% (maximum). Sent as 2 hex digits via
        the 'sv' command.
        """
        pct = int(max(1, min(100, round(percent))))
        # 'sv' wants 2 hex chars representing 0x01..0x64 (1..100)
        data = bytes([pct >> 4 & 0x0F, pct & 0x0F])  # not used - we format as hex string
        # Packet: <addr>sv<hex(pct):2-digit upper>
        packet = (
            str(self.address).encode('utf-8')
            + b'sv'
            + f"{pct:02X}".encode('utf-8')
            + b'\n'
        )
        self._conn.reset_input_buffer()
        self._conn.write(packet)
        time.sleep(0.05)
        # Read status response
        response = b''
        start = time.time()
        while time.time() - start < 0.5:
            chunk = self._conn.read(8192)
            response += chunk
            if response.endswith(b'\r\n'):
                break
            time.sleep(0.01)
        if self.verbose:
            print(f"[{self.port}] set_velocity({pct}%): {response!r}")
        return pct

    def clean(self, cycles=5, settle_s_each=0.7):
        """
        Perform cleaning cycles (full rotations back and forth).
        
        Args:
            cycles: Number of complete cycles
            settle_s_each: Settle time for each half-cycle
        """
        if self.verbose:
            print(f"[{self.port}] Cleaning: {cycles} cycles of ±360°")
        
        for i in range(cycles):
            if self.verbose:
                print(f"[{self.port}] Cycle {i+1}/{cycles}: Forward")
            self.shift_angle(+360.0, settle_s=settle_s_each)
            
            if self.verbose:
                print(f"[{self.port}] Cycle {i+1}/{cycles}: Backward")
            self.shift_angle(-360.0, settle_s=settle_s_each)
    
    def disconnect(self):
        """Close serial connection (alias for close)."""
        self.close()
    
    def close(self):
        """Close serial connection."""
        if hasattr(self, '_conn') and self._conn and self._conn.is_open:
            self._conn.close()
            if self.verbose:
                print(f"[{self.port}] Disconnected")
