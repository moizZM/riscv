import carla
import socket
import json
import cv2
import numpy as np
import time

#udp configs
UDP_IP = ""  #Ip of Computer 2
UDP_PORT_SEND = 9000
UDP_PORT_RECEIVE = 9001

send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
recv_sock.bind(("", UDP_PORT_RECEIVE))
recv_sock.setblocking(False)

#Carla client server
client = carla.Client('localhost', 2000)
client.set_timeout(5.0)
world = client.get_world()
blueprint_lib = world.get_blueprint_library()

#Traffic manager setup
tm = client.get_trafficmanager()
tm.set_synchronous_mode(False)  # or True if you're using sync mode
tm.set_global_distance_to_leading_vehicle(2.5)

#spawning ego vehicle
vehicle_bp = blueprint_lib.find('vehicle.tesla.model3')
spawn_point = world.get_map().get_spawn_points()[0]
vehicle = world.spawn_actor(vehicle_bp, spawn_point)
vehicle.set_autopilot(True, tm.get_port())

#Traffic Manager code to ignore walkers, traffic light
tm.ignore_lights_percentage(vehicle, 100.0)
tm.ignore_walkers_percentage(vehicle, 100.0)


#Camera setup for viewing
camera_bp = blueprint_lib.find('sensor.camera.rgb')
camera_bp.set_attribute('image_size_x', '640')
camera_bp.set_attribute('image_size_y', '480')
camera_bp.set_attribute('fov', '90')
camera_transform = carla.Transform(carla.Location(x=-6, z=3), carla.Rotation(pitch=-10))
camera = world.spawn_actor(camera_bp, camera_transform, attach_to=vehicle)

frame = None
def camera_callback(image):
    global frame
    array = np.frombuffer(image.raw_data, dtype=np.uint8)
    array = np.reshape(array, (image.height, image.width, 4))
    frame = array[:, :, :3]

camera.listen(camera_callback)

#Pedestrian detection using forward_vector and dot product
DETECTION_RANGE = 10.0  # meters
def detect_pedestrian(ego_transform, walkers):
    for walker in walkers:
        loc = walker.get_location()
        direction = loc - ego_transform.location
        distance = direction.length()
        if distance > DETECTION_RANGE:
            continue
        forward_vector = ego_transform.get_forward_vector()
        direction = direction.make_unit_vector()
        dot = forward_vector.x * direction.x + forward_vector.y * direction.y + forward_vector.z * direction.z
        if dot > 0.7:
            return True, distance
    return False, None

#Main loop
try:
    while True:
        if frame is not None:
            cv2.imshow("Third-Person Camera", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        #calculation of current speed
        vel = vehicle.get_velocity()
        speed = 3.6 * (vel.x**2 + vel.y**2 + vel.z**2)**0.5  # in km/h

        #Check for pedestrians
        ego_transform = vehicle.get_transform()
        walkers = world.get_actors().filter('walker.pedestrian.*')
        detected, distance = detect_pedestrian(ego_transform, walkers)

        #sending data using udp
        data = {
            "speed": round(speed, 2),
            "pedestrian_detected": detected,
            "distance": round(distance, 2) if distance else None
        }
        send_sock.sendto(json.dumps(data).encode(), (UDP_IP, UDP_PORT_SEND))

        #listen for brake or resume commands
        try:
            msg, _ = recv_sock.recvfrom(1024)
            cmd = msg.decode()
            if cmd == "brake":
                print("[CARLA] Brake message received.")
                vehicle.set_autopilot(False)
                vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            elif cmd == "resume":
                print("[CARLA] Resume message received.")
                vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=0.0))
                time.sleep(0.1)
                vehicle.set_autopilot(True, tm.get_port())
        except BlockingIOError:
            pass

finally:
    camera.stop()
    vehicle.destroy()
    cv2.destroyAllWindows()
