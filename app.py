import cv2
import pytesseract
import pandas as pd
from datetime import datetime
import re
import os
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db
from fuzzywuzzy import fuzz
from concurrent.futures import ThreadPoolExecutor
import time

# Initialize Firebase
cred = credentials.Certificate(r"C:\Vehicle_Parking\credentials\serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://cityparkpro-default-rtdb.europe-west1.firebasedatabase.app/'
})

# Firebase paths
BOOKING_REF = 'booking'
GATE_CONTROL_REF = 'gateControl'
PROCESSED_BOOKINGS_REF = 'processedBookings'  # Track processed bookings
UNLOCK_DURATION = 10  # Seconds to keep gate unlocked

# Function to normalize plate strings
def normalize_plate(plate):
    """Remove spaces and special characters, convert to uppercase"""
    return re.sub(r'[^A-Z0-9]', '', plate.upper())

# Function to load/refresh plates from Firebase
def refresh_firebase_plates():
    bookings = db.reference(BOOKING_REF).get()
    if not bookings:
        return []
    
    plates = []
    for bid, booking in bookings.items():
        if 'vehicleNumber' in booking:
            plates.append({
                'raw': booking['vehicleNumber'],
                'normalized': normalize_plate(booking['vehicleNumber']),
                'booking_id': bid,
                'checkIn': booking.get('checkIn', ''),
                'checkOut': booking.get('checkOut', '')
            })
    return plates

# Function to check if booking has been processed
def is_booking_processed(booking_id):
    processed = db.reference(f"{PROCESSED_BOOKINGS_REF}/{booking_id}").get()
    return processed is not None

# Function to mark booking as processed
def mark_booking_processed(booking_id):
    ref = db.reference(f"{PROCESSED_BOOKINGS_REF}/{booking_id}")
    ref.set({
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

# --------- NEW UPDATED control_gate FUNCTION ---------
def control_gate(booking_id, action):
    """Overwrite the entire gateControl node with a new booking command"""
    gate_ref = db.reference(GATE_CONTROL_REF)  # Root of gateControl
    command = {
        booking_id: {
            'action': action,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'duration': UNLOCK_DURATION if action == 'unlock' else 0
        }
    }
    gate_ref.set(command)  # Overwrite gateControl with the new booking command
    print(f"Overwrote gateControl with booking {booking_id} -> {action}")



# Fuzzy matching function
def find_best_match(detected_plate, firebase_plates, threshold=85):
    """Find the best match using fuzzy string matching"""
    detected_normalized = normalize_plate(detected_plate)
    
    # First try exact match (fastest)
    for plate in firebase_plates:
        if detected_normalized == plate['normalized']:
            return plate
    
    # If no exact match, try fuzzy matching
    best_match, best_score = None, 0
    for plate in firebase_plates:
        score = fuzz.ratio(detected_normalized, plate['normalized'])
        if score > best_score and score >= threshold:
            best_score = score
            best_match = plate
    
    return best_match

# Initialize
firebase_plates = refresh_firebase_plates()
print(f"Initial load: {len(firebase_plates)} plates from Firebase")

# Video capture setup
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
cap = cv2.VideoCapture(0)
output_folder = "detected_plates"
os.makedirs(output_folder, exist_ok=True)

# DataFrame setup - only store first recognition per booking
plate_data = pd.DataFrame(columns=["Timestamp", "Detected Plate", "Matched Plate", 
                                 "Match Score", "Booking ID", "Gate Action", "Image Path"])

# Image processing functions
def preprocess_image(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 11, 17, 17)
    edged = cv2.Canny(gray, 30, 200)
    return edged

def detect_number_plate(frame):
    processed_frame = preprocess_image(frame)
    contours, _ = cv2.findContours(processed_frame, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]
    
    plate = None
    plate_coords = None
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.018 * perimeter, True)
        if len(approx) == 4:
            x, y, w, h = cv2.boundingRect(approx)
            plate = frame[y:y + h, x:x + w]
            plate_coords = (x, y, w, h)
            break
    return plate, plate_coords

def validate_number_plate(text):
    pattern = r"[A-Za-z0-9]{2,5}[\s\-=+—][A-Za-z0-9]{4}" 
    return re.search(pattern, text) is not None

# Main processing loop
frame_count = 0
refresh_interval = 300  
match_threshold = 80    

with ThreadPoolExecutor(max_workers=4) as executor:
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        
        # Periodic Firebase update
        if frame_count % refresh_interval == 0:
            firebase_plates = refresh_firebase_plates()
            print(f"Refreshed Firebase plates. Current count: {len(firebase_plates)}")

        plate, plate_coords = detect_number_plate(frame)

        if plate is not None:
            plate_text = pytesseract.image_to_string(plate, config='--psm 8').strip()
            
            if plate_text and validate_number_plate(plate_text):
                # Fuzzy matching in parallel
                future = executor.submit(
                    find_best_match,
                    plate_text,
                    firebase_plates,
                    match_threshold
                )
                best_match = future.result()
                
                current_time = time.time()
                gate_action = None
                
                if best_match:
                    match_score = fuzz.ratio(normalize_plate(plate_text), best_match['normalized'])
                    is_match = match_score >= match_threshold
                    
                    if is_match:
                        # Check if this booking has been processed before
                        if is_booking_processed(best_match['booking_id']):
                            status_color = (0, 255, 255)  # Cyan
                            status_text = "BOOKING ALREADY PROCESSED"
                            gate_action = 'already_processed'
                        else:
                            # Verify booking time window
                            now = datetime.now().strftime("%H:%M")
                            if (now >= best_match['checkIn'] and now <= best_match['checkOut']):
                                control_gate(best_match['booking_id'], 'unlock')
                                mark_booking_processed(best_match['booking_id'])
                                gate_action = 'unlock'
                                status_color = (0, 255, 0)  # Green
                                status_text = f"MATCH ({match_score}%) - GATE UNLOCKED"
                            else:
                                status_color = (255, 255, 0)  # Yellow
                                status_text = "MATCH (OUTSIDE BOOKING HOURS)"
                                gate_action = 'denied'
                    else:
                        status_color = (0, 0, 255)  # Red
                        status_text = "NO MATCH"
                else:
                    status_color = (0, 0, 255)  # Red
                    status_text = "NO MATCH"
                
                print(f"Detected: {plate_text} | {status_text} | Booking: {best_match['booking_id'] if best_match else 'N/A'}")

                # Only save data if this is the first recognition for this booking
                if gate_action not in ['already_processed']:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    image_filename = f"{output_folder}/plate_{timestamp}.png"
                    cv2.imwrite(image_filename, plate)

                    plate_data = pd.concat([plate_data, pd.DataFrame({
                        "Timestamp": [datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
                        "Detected Plate": [plate_text],
                        "Matched Plate": [best_match['raw'] if best_match else None],
                        "Match Score": [match_score if best_match else 0],
                        "Booking ID": [best_match['booking_id'] if best_match else None],
                        "Gate Action": [gate_action],
                        "Image Path": [image_filename]
                    })], ignore_index=True)

                # Visual feedback
                if plate_coords:
                    x, y, w, h = plate_coords
                    cv2.rectangle(frame, (x, y), (x + w, y + h), status_color, 2)
                    cv2.putText(frame, plate_text, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
                    cv2.putText(frame, status_text, (x, y + h + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
                    if best_match and best_match['raw'] != plate_text:
                        cv2.putText(frame, f"Original: {best_match['raw']}", (x, y + h + 50), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, status_color, 1)

        cv2.imshow("Number Plate Recognition", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

cap.release()
cv2.destroyAllWindows()

# Save final results
plate_data.to_csv("detected_number_plates.csv", index=False)
print(f"Processing complete. {len(plate_data)} unique bookings processed. Results saved to CSV.")
