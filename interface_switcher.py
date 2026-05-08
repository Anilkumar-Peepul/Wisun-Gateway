import subprocess
import re
import time
import logging

# Set up logging
logging.basicConfig(
    filename="/var/log/interface_switcher.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def run_command(command):
    """Run a shell command with sudo and handle errors."""
    try:
        result = subprocess.run(f"sudo {command}", shell=True, check=True, capture_output=True, text=True)
        print(f"Command '{command}' succeeded: {result.stdout}")
        return result.stdout, True
    except subprocess.CalledProcessError as e:
        print(f"ERROR : Command '{command}' failed: {e.stderr}")
        return e.stderr, False

def set_permanent_dns():
    """Force Google Public DNS in /etc/resolv.conf and lock it."""
    dns_config = "nameserver 8.8.8.8\nnameserver 8.8.4.4\n"
    try:
        # Unlock resolv.conf if previously immutable
        subprocess.run("sudo chattr -i /etc/resolv.conf", shell=True, check=False)

        # Write DNS to resolv.conf
        with open("/etc/resolv.conf", "w") as f:
            f.write(dns_config)
        
        # Lock the file so other services don't overwrite it
        subprocess.run("sudo chattr +i /etc/resolv.conf", shell=True, check=False)
        print("Permanent DNS set to Google (8.8.8.8, 8.8.4.4)")
    except Exception as e:
        print(f"Failed to set permanent DNS: {str(e)}")

def get_network_info():
    try:
        interfaces = subprocess.check_output(
            "ip link show | grep '^[0-9]' | awk '{print $2}' | sed 's/://'",
            shell=True
        ).decode().split()
        result = {}
        for iface in interfaces:
            try:
                ip_output = subprocess.check_output(f"ip addr show {iface} | grep inet", shell=True).decode()
                ip = re.search(r'inet (\d+\.\d+\.\d+\.\d+(?:/\d+)?)(?:\s+peer\s+\S+)?', ip_output)
                ip_addr = ip.group(1) if ip else "No IP"
            except subprocess.CalledProcessError:
                ip_addr = "No IP"
            
            gateway_addr = "No Gateway"
            try:
                route_output = subprocess.check_output(f"ip route show dev {iface}", shell=True).decode()
                gateway = re.search(r'default via (\S+)', route_output)
                if gateway:
                    gateway_addr = gateway.group(1)
                else:
                    gateway = re.search(r'(\d+\.\d+\.\d+\.\d+)\s+proto\s+kernel\s+scope\s+link', route_output)
                    if gateway:
                        gateway_addr = gateway.group(1)
            except subprocess.CalledProcessError:
                pass
            
            result[iface] = {"IP": ip_addr, "Gateway": gateway_addr}
        print(f"Network info: {result}")
        return result
    except Exception as e:
        print(f"Error in get_network_info: {str(e)}")
        return {}

def test_connectivity(iface):
    """Test connectivity by pinging 8.8.8.8 via the specified interface."""
    try:
        output = subprocess.run(f"ping -c 5 -W 5 -I {iface} 8.8.8.8", shell=True, check=True, capture_output=True, text=True)
        stdout = output.stdout
        
        match = re.search(r'(\d+)\s+received', stdout)
        success = match and int(match.group(1)) > 0  # Define success based on packets received
        print(f"Ping test for {iface}: {'Successful' if success else 'Failed'} - {output.stdout}")
        return success
    except subprocess.CalledProcessError as e:
        print(f"Ping test for {iface} failed: {e.stderr}")
        return False

def restart_ppp_service():
    """Restart quectel-ppp.service to ensure ppp0 is active."""
    output, success = run_command("systemctl restart quectel-monitor.service")
    time.sleep(7)
    output, success = run_command("systemctl restart quectel-ppp.service")
    time.sleep(7)

    if success:
        print("Successfully restarted quectel-ppp.service")
        time.sleep(5)  # Wait for ppp0 to initialize
    else:
        print(f"Failed to restart quectel-ppp.service: {output}")
    return success

def set_default_route(iface, gateway=None):
    """Set default route for the specified interface."""
    if iface == "ppp0":
        output, success = run_command(f"ip route add default dev {iface}")
    else:
        output, success = run_command(f"ip route add default via {gateway} dev {iface}")
    print(f"Set default route for {iface}: {'Success' if success else f'Failed: {output}'}")
    return output, success

def delete_all_default_routes():
    """Delete all existing default routes."""
    output, success = run_command("ip route show | grep '^default'")
    if not success:
        print("No default routes to delete")
        return "No default routes to delete", True
    while True:
        output, success = run_command("sudo ip route del default")
        if not success:
            break
    print("All default routes deleted")
    return "All default routes deleted", True

def connect_wifi(gateway):
    ssid = "JioFi_2EF513A"  # Use the correct SSID
    password = "j2khi56hh9"  # Use the correct password
    # Turn on the Wi-Fi radio
    output, success = run_command('nmcli radio wifi on')
    if not success:
        print(f"Failed to enable Wi-Fi radio: {output}")
    output, success = run_command('nmcli dev wifi rescan')
    if not success:
        print(f"Wi-Fi rescan failed: {output}")
    output, success = run_command('nmcli dev wifi list')
    if not success:
        print(f"Wi-Fi list failed: {output}")
    output, success = run_command(f'sudo nmcli dev wifi connect "{ssid}" password "{password}"')
    if success:
        print(f"Successfully connected to Wi-Fi {ssid}")
    else:
        print(f"Failed to connect to Wi-Fi {ssid}: {output}")
    
    # Refresh network info after connection attempt
    network_info = get_network_info()
    for iface, info in network_info.items():
        if iface == 'wlan0':
            gateway = info['Gateway']
        print(f"Interface: {iface}, IP: {info['IP']}, Gateway: {info['Gateway']}")
    
    success1 = test_connectivity('wlan0')
    print(f"RESULT: wlan0 connectivity test: {'Successful' if success1 else 'Failed'}")
    if success1 and gateway != "No Gateway":
        output, success = delete_all_default_routes()
        if success:
            output, success = set_default_route("wlan0", gateway)
            if not success:
                print(f"Failed to set wlan0 as default: {output}")
                print(f"Failed to set wlan0 as default: {output}")
    else:
        print(f"wlan0 connection failed or no gateway assigned")
    return gateway
    
def switch_interface():
    """Switch default route between wlan0 and ppp0 based on connectivity."""
    set_permanent_dns()   # Always reset and lock Google DNS
    gateway = "192.168.1.1"  # Adjust if wlan0 gateway differs
    current_default = None
    
    while True:
        try:
            print("Checking network interfaces...")
            network_info = get_network_info()
            for iface, info in network_info.items():
                print(f"Interface: {iface}, IP: {info['IP']}, Gateway: {info['Gateway']}")
            
            # Check wlan0 status
            wlan0_exists = "wlan0" in network_info
            wlan0_has_ip = wlan0_exists and network_info["wlan0"]["IP"] != "No IP"
            wlan0_works = wlan0_has_ip and test_connectivity("wlan0")
            
            # Check ppp0 status
            ppp0_exists = "ppp0" in network_info
            ppp0_has_ip = ppp0_exists and network_info["ppp0"]["IP"] != "No IP"
            ppp0_works = ppp0_exists and test_connectivity('ppp0')
            
            # Get current default route
            route_output, _ = run_command("ip route show | grep '^default'")
            current_default = "wlan0" if "dev wlan0" in route_output else "ppp0" if "dev ppp0" in route_output else None
            print(f"Current default route: {current_default if current_default else 'None'}")
            
            # Case 1: wlan0 is working, keep or set it as default
            if wlan0_works:
                if current_default != "wlan0":
                    print("wlan0 is working, setting it as default route...")
                    output, success = delete_all_default_routes()
                    
                    if success:
                        output, success = set_default_route("wlan0", gateway)
                        if not success:
                            print(f"ERROR -->Failed to set wlan0 as default: {output}")  
                else:
                    result  = run_command("sudo ip route del default dev ppp0")
                    print("ERROR --> wlan0 is already the default route and working")
            
            # Case 2: wlan0 not working, switch to ppp0
            elif ppp0_exists:
                if not ppp0_has_ip:
                    print("PPP 1")
                    print("ppp0 has no IP, restarting quectel-ppp.service...")
                    restart_ppp_service()
                    network_info = get_network_info()  # Refresh info
                    ppp0_has_ip = "ppp0" in network_info and network_info["ppp0"]["IP"] != "No IP"
                
                if ppp0_has_ip and ppp0_works and current_default != "ppp0":
                    print("PPP 2")
                    print("wlan0 not working, setting ppp0 as default route...")
                    output, success = delete_all_default_routes()
                    if success:
                        output, success = set_default_route("ppp0")
                        if test_connectivity("ppp0"):
                            print("ppp0 is working")
                        else:
                            print("ppp0 default route set but ping failed")
                elif ppp0_has_ip and not ppp0_works:
                    print("PPP0 has IP, but not Working")
                    restart_ppp_service()
                    
                elif ppp0_has_ip and ppp0_works and current_default == "ppp0":
                    print("ppp0 is already the default route")
                    res=run_command("sudo systemctl restart dhcpcd")
                    connect_wifi(gateway)
                else:
                    restart_ppp_service()   
                    connect_wifi(gateway)
                    print("ppp0 not available after restart")
            else:
                print("Neither wlan0 nor ppp0 is working or available")
                restart_ppp_service()
                res = run_command("sudo systemctl restart dhcpcd")
                connect_wifi(gateway)
            
            # Wait before next check
            print("Waiting 10 seconds before next check...")
            time.sleep(10)
        except Exception as e:
            print(f"ERROR in switch_interface: {str(e)}")
            time.sleep(10)  # Continue loop on error

if __name__ == "__main__":
    print("Starting interface switcher service...")
    try:
        switch_interface()
    except KeyboardInterrupt:
        print("Script terminated by user.")

