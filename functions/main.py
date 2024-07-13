# The Cloud Functions for Firebase SDK to create Cloud Functions and set up triggers.
from firebase_functions import firestore_fn, https_fn

# The Firebase Admin SDK to access Cloud Firestore.
from firebase_admin import initialize_app, firestore
import google.cloud.firestore
import os
import datetime

import google.generativeai as genai
import vertexai
from vertexai.preview import reasoning_engines
from vertexai.generative_models import (
    FunctionDeclaration,
    GenerationConfig,
    GenerativeModel,
    Tool,
    Part,
)

app = initialize_app()

# db = firestore.client()

AUTHOR_ID = "bcYT3imwF7QY6OdRnRyAY19K3Zz1"


@https_fn.on_request()
def addmessage(req: https_fn.Request) -> https_fn.Response:
    """Take the text parameter passed to this HTTP endpoint and insert it into
    a new document in the messages collection."""
    # Grab the text parameter.
    original = req.args.get("text")
    if original is None:
        return https_fn.Response("No text parameter provided", status=400)

    firestore_client: google.cloud.firestore.Client = firestore.client()

    # Push the new message into Cloud Firestore using the Firebase Admin SDK.
    _, doc_ref = firestore_client.collection("messages").add({"original": original})

    # Send back a message that we've successfully written the message
    return https_fn.Response(f"Message with ID {doc_ref.id} added.")


@firestore_fn.on_document_created(document="messages/{pushId}")
def makeuppercase(
    event: firestore_fn.Event[firestore_fn.DocumentSnapshot | None],
) -> None:
    """Listens for new documents to be added to /messages. If the document has
    an "original" field, creates an "uppercase" field containg the contents of
    "original" in upper case."""

    # Get the value of "original" if it exists.
    if event.data is None:
        return
    try:
        original = event.data.get("original")
    except KeyError:
        # No "original" field, so do nothing.
        return

    # Set the "uppercase" field.
    print(f"Uppercasing {event.params['pushId']}: {original}")
    upper = original.upper()
    event.data.reference.update({"uppercase": upper})


@firestore_fn.on_document_created(
    document="messages/{pushId}", secrets=["GOOGLE_API_KEY"]
)
def makeaboutme(
    event: firestore_fn.Event[firestore_fn.DocumentSnapshot | None],
) -> None:
    """Listens for new documents to be added to /messages. If the document has
    an "original" field, creates an "uppercase" field containg the contents of
    "original" in upper case."""

    GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel("gemini-pro")

    # Get the value of "original" if it exists.
    if event.data is None:
        return
    try:
        original = event.data.get("original")
    except KeyError:
        # No "original" field, so do nothing.
        return

    response = model.generate_content(
        "Eres un asistente que ayuda a los usuarios de una aplicación de servicios a generar la descripción para su profile. Genera una descripción atractiva para las personas que requieren dicho servicio en no mas de 50 palabras basado en las siguientes características: "
        + original
    )

    event.data.reference.update({"aboutme": response.text})


@firestore_fn.on_document_created(
    document="rooms/{roomId}/messages/{messageId}", secrets=["GOOGLE_API_KEY"]
)
def chat_with_user(
    event: firestore_fn.Event[firestore_fn.DocumentSnapshot | None],
) -> None:
    """Listens for new documents to be added to /rooms/wh6JjGjt1tP1ubfTQrA5/messages. If the document has
    an "original" field, creates an "uppercase" field containg the contents of
    "original" in upper case."""

    db = firestore.client()

    get_service_categories = FunctionDeclaration(
        name="get_service_categories",
        description="Get service categories from the database",
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    yomap_tool = Tool(
        function_declarations=[get_service_categories],
    )

    def get_service_categories_from_firebase():
        tags_ref = (
            db.collection("categories")
            # .where(filter=FieldFilter("active", "==", True))
            # .where(filter=FieldFilter("rating", ">=", 3))
        )
        docs = tags_ref.stream()

        tags = []
        for doc in docs:
            if "name" in doc.to_dict().keys():
                tags.append(doc.to_dict()["name"])
        return tags

    function_handler = {
        "get_service_categories": get_service_categories_from_firebase,
    }

    GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
    genai.configure(api_key=GOOGLE_API_KEY)
    model = GenerativeModel(
        "gemini-1.5-pro-001",
        generation_config=GenerationConfig(temperature=0),
        tools=[yomap_tool],
    )

    # Get the room

    room_a_ref = db.collection("rooms").document(event.params["roomId"]).get()
    userIds = room_a_ref.get("userIds")

    if AUTHOR_ID in userIds:

        # Get the value of "original" if it exists.
        if event.data is None:
            return
        try:
            authorId = event.data.get("authorId")
        except KeyError:
            # No "original" field, so do nothing.
            return

        if authorId != AUTHOR_ID:
            chat = model.start_chat()

            prompt = """Eres un asistente que responde cualquier pregunta que
                te hagan los usuarios. Si la pregunta esta relacionada con
                alguna de las tools disponibles, usa siempre la tool primero.
                Caso contrario puedes responder usando tu propia informacion.
                Cuando sea posible organiza la respuesta en bulletpoints.
                Reponde la siguiente pregunta en no mas de 50 palabras de ser
                posible: """

            message = event.data.get("text")

            prompt += message

            # Send a chat message to the Gemini API
            response = chat.send_message(prompt)

            # Extract the function call response
            function_call = response.candidates[0].content.parts[0].function_call

            # Check for a function call or a natural language response
            if function_call.name in function_handler.keys():
                # Extract the function call
                function_call = response.candidates[0].content.parts[0].function_call

                # Extract the function call name
                function_name = function_call.name

                if function_name == "get_service_categories":
                    # Invoke a function that calls an external API
                    function_api_response = function_handler[function_name]()

                # Send the API response back to Gemini, which will generate a natural language summary or another function call
                response = chat.send_message(
                    Part.from_function_response(
                        name=function_name,
                        response={"content": function_api_response},
                    ),
                )

            # Get reference to the messages collection
            room_a_ref = db.collection("rooms").document(event.params["roomId"])
            message_ref = room_a_ref.collection("messages").document()

            message_ref.set(
                {
                    "text": response.text,
                    "type": "text",
                    "authorId": AUTHOR_ID,
                    "createdAt": datetime.datetime.now(tz=datetime.timezone.utc),
                    "updatedAt": datetime.datetime.now(tz=datetime.timezone.utc),
                }
            )
