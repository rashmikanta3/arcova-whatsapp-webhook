# arcova-whatsapp-webhook
web hook with whatsupapi

# The SQL query to create Table

CREATE TABLE IF NOT EXISTS messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phone TEXT NOT NULL,
    sender TEXT NOT NULL,
    message_text TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

-- Enable Row Level Security and allow full access for the API keys
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow full access" ON messages FOR ALL USING (true) WITH CHECK (true);


#Template description

Case 1 (Image only, no body variables): Image_URL is filled, Var1–Var5 are empty. The code creates only a header component.

Case 2 (Plain text, no variables, no image): Both Image_URL and Var1–Var5 are empty. The code leaves components completely empty, matching Meta's requirement for static templates.

Case 3 (Variables only, no image): Image_URL is empty, while Var1 through Var5 are filled. The code skips header and sends only the body component.

Case 4 (Both image and variables): Image_URL and Var1–Var5 are both populated. The code includes both header and body components in the single payload.
