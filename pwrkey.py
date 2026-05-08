import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)     # Use BCM pin numbering
GPIO.setup(25, GPIO.OUT)   # Set GPIO18 as output
GPIO.output(25,GPIO.HIGH)
time.sleep(3)
GPIO.output(25, GPIO.LOW) # Turn on
#time.sleep(3)
#GPIO.output(25, GPIO.LOW)  # Turn off
#time.sleep(5)
GPIO.cleanup()             # Reset all GPIOs
