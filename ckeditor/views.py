import os
from urlparse import urlparse, urlunparse
import sys
import re
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.template import RequestContext
            
try: 
    from PIL import Image, ImageOps 
except ImportError: 
    import Image, ImageOps

try:
    from django.views.decorators.csrf import csrf_exempt
except ImportError:
    # monkey patch this with a dummy decorator which just returns the same function
    # (for compatability with pre-1.1 Djangos)
    def csrf_exempt(fn):
        return fn
        
THUMBNAIL_SIZE = (75, 75)

# Non-image file icons, matched from top to bottom
fileicons_path = '%sfile-icons/' % settings.CKEDITOR_MEDIA_PREFIX
CKEDITOR_FILEICONS = getattr(settings, 'CKEDITOR_FILEICONS', [
    ('\.swf$', fileicons_path + 'swf.png'),
    ('\.pdf$', fileicons_path + 'pdf.png'),
    ('\.doc$|\.docx$|\.odt$', fileicons_path + 'doc.png'),
    ('\.txt$', fileicons_path + 'txt.png'),
    ('\.zip$|\.rar$|\.tar$|\.tar\..+$', fileicons_path + 'zip.png'),
    ('\.ppt$', fileicons_path + 'ppt.png'),
    ('\.xls$', fileicons_path + 'xls.png'),
    ('.*', fileicons_path + 'file.png'), # Default
])

def get_available_name(name):
    """
    Returns a filename that's free on the target storage system, and
    available for new content to be written to.
    """
    dir_name, file_name = os.path.split(name)
    file_root, file_ext = os.path.splitext(file_name)
    # If the filename already exists, keep adding an underscore (before the
    # file extension, if one exists) to the filename until the generated
    # filename doesn't exist.
    while os.path.exists(name):
        file_root += '_'
        # file_ext includes the dot.
        name = os.path.join(dir_name, file_root + file_ext)
    return name

def get_thumb_filename(file_name):
    """
    Generate thumb filename by adding _thumb to end of filename before . (if present)
    """
    return '%s_thumb%s' % os.path.splitext(file_name)

def get_icon_filename(file_name):
    """
    Return the path to a file icon that matches the file name.
    """
    for regex, iconpath in CKEDITOR_FILEICONS:
        if re.search(regex, file_name, re.I):
            return iconpath

def create_thumbnail(filename):
    image = Image.open(filename)
    
    # Convert to RGB if necessary
    # Thanks to Limodou on DjangoSnippets.org
    # http://www.djangosnippets.org/snippets/20/
    if image.mode not in ('L', 'RGB'):
        image = image.convert('RGB')
       
    # scale and crop to thumbnail
    imagefit = ImageOps.fit(image, THUMBNAIL_SIZE, Image.ANTIALIAS)
    imagefit.save(get_thumb_filename(filename))
        
def get_media_url(path):
    """
    Determine system file's media URL.
    """
    upload_prefix = getattr(settings, "CKEDITOR_UPLOAD_PREFIX", None)
    if upload_prefix:
        url = upload_prefix + path.replace(settings.CKEDITOR_UPLOAD_PATH, '')
    else:
        url = settings.MEDIA_URL + path.replace(settings.MEDIA_ROOT, '')

    # remove multiple forward-slashes from the path portion of the url
    url_parts    = list( urlparse( url ) )              # break url into a list
    url_parts[2] = re.sub( '\/+', '/', url_parts[2] )   # replace two or more slashes with a single slash
    url = urlunparse( url_parts )                       # reconstruct the url
   
    return url

def get_upload_filename(upload_name, user):
    
    if isinstance(upload_name, unicode):
        upload_name = upload_name.encode('utf-8')
    
    # If CKEDITOR_RESTRICT_BY_USER is True upload file to user specific path.
    if getattr(settings, 'CKEDITOR_RESTRICT_BY_USER', False):
        user_path = user.username
    else:
        user_path = ''

    # Generate date based path to put uploaded file.
    date_path = datetime.now().strftime('%Y/%m/%d')
    
    # Complete upload path (upload_path + date_path).
    upload_path = os.path.join(settings.CKEDITOR_UPLOAD_PATH, user_path, date_path)
   
    # Make sure upload_path exists.
    if not os.path.exists(upload_path):
        os.makedirs(upload_path)
    
    # Get available name and return.
    return get_available_name(os.path.join(upload_path, upload_name))
     
    
@csrf_exempt
def upload(request):
    """
    Uploads a file and send back its URL to CKEditor.

    TODO:
        Validate uploads
    """
    # Get the uploaded file from request.
    upload = request.FILES['upload']
    upload_ext = os.path.splitext(upload.name)[1]
   
    # Open output file in which to store upload. 
    upload_filename = get_upload_filename(upload.name, request.user)
    out = open(upload_filename, 'wb+')

    # Iterate through chunks and write to destination.
    for chunk in upload.chunks():
        out.write(chunk)
    out.close()
    
    try:
        create_thumbnail(upload_filename)
    except IOError, OverflowError:
        # Assume file not an image
        pass

    # Respond with Javascript sending ckeditor upload url.
    url = get_media_url(upload_filename)
    return HttpResponse(u"""
    <script type='text/javascript'>
        window.parent.CKEDITOR.tools.callFunction(%s, '%s');
    </script>""" % (request.GET['CKEditorFuncNum'], url.decode('utf-8')))

def get_image_browse_urls(user=None):
    """
    Recursively walks all dirs under upload dir and generates a list of
    thumbnail and full image URL's for each file found.
    """
    images = []
    
    # If a user is provided and CKEDITOR_RESTRICT_BY_USER is True,
    # limit images to user specific path, but not for superusers.
    if user and not user.is_superuser and getattr(settings, 'CKEDITOR_RESTRICT_BY_USER', False):
        user_path = user.username
    else:
        user_path = ''

    browse_path = os.path.join(settings.CKEDITOR_UPLOAD_PATH, user_path)
    
    for root, dirs, files in os.walk(browse_path):
        for filename in [ os.path.join(root, x) for x in files ]:
            # bypass for thumbs
            if '_thumb' in filename:
                continue
            
            thumb_path = get_thumb_filename(filename)
            if os.path.exists(thumb_path):
                visible_filename = None
                is_image = True
                thumb_path = get_media_url(thumb_path)
            else:
                # File may not be an image
                visible_filename = unicode(os.path.split(filename)[1], 'utf-8')
                if len(visible_filename) > 20:
                    visible_filename = visible_filename[0:19] + '...'
                is_image = False
                thumb_path = get_icon_filename(filename)
            
            images.append({
                'thumb': thumb_path,
                'src': get_media_url(filename),
                'visible_filename': visible_filename,
                'is_image': is_image
            })

    return images
    
def browse(request):
    context = RequestContext(request, {
        'images': get_image_browse_urls(request.user),
    })
    return render_to_response('browse.html', context)
