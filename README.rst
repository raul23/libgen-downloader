====================================
libgen-downloader (WORK-IN-PROGRESS)
====================================
Download books from Library Genesis (libgen)

.. contents:: **Contents**
   :depth: 3
   :local:
   :backlinks: top

Disclaimer
==========
`:warning:`

  **Disclaimer**

  This application is intended for personal, educational, and fair use purposes only. The developer and 
  contributors to this application do not endorse or encourage the illegal downloading or distribution of copyrighted 
  materials. Users are solely responsible for ensuring that their use of this application complies with applicable 
  copyright laws and regulations.
  
  The developer and contributors disclaim any responsibility for the misuse of this application for the purpose of 
  downloading copyrighted material without proper authorization. The application is designed to facilitate legal and 
  authorized downloads, and users should respect the rights of authors, publishers, and copyright holders.
  
  By using this application, you agree to use it in compliance with all relevant copyright laws, and you acknowledge that 
  any unauthorized use may constitute a violation of such laws.
  
  This disclaimer may be subject to updates, and users are encouraged to review it periodically for any changes.

Description
===========

Dependencies
============
* **Platforms:** Linux, macOS
* **Python** >=3.8
* **Python packages:**
  
  * `Beautiful Soup`_
  * `pyrfc6266`_
  * `Requests`_

Installation instructions
=========================
Install
-------
1. It is highly recommended to install ``bookdl`` in a virtual
   environment using for example `venv`_ or `conda`_.

2. Make sure to update *pip*::

   $ pip install --upgrade pip

.. 3. Install the package ``bookdl`` (released version **0.1.0a0**) with *pip*::

   .. $ pip install git+https://github.com/raul23/libgen-downloader@v0.1.0a0#egg=libgen-downloader

   .. It will install the dependencies if they are not already found in your system.

`:warning:`

   Make sure that *pip* is working with the correct Python version. It might be
   the case that *pip* is using Python 2.x You can find what Python version
   *pip* uses with the following::

      $ pip -V

   If *pip* is working with the wrong Python version, then try to use *pip3*
   which works with Python 3.x

.. `:information_source:`

   .. To install the **bleeding-edge version** of the ``darth_vader_rpi`` package::

      .. $ pip install git+https://github.com/raul23/libgen-downloader#egg=libgen-downloader

   .. However, this latest version is not as stable as the released version but you
   .. get the latest features being implemented.

.. **Test installation**

.. Test your installation by importing ``bookdl`` and printing its version::

   .. $ python -c "import bookdl; print(bookdl.__version__)"

Uninstall
---------
.. To uninstall the package ``bookdl``::

   .. $ pip uninstall bookdl

Application usage
=================
Searching books
---------------

Downloading books
-----------------

Pausing/Resuming downloads
--------------------------

Canceling downloads
-------------------

Logging
-------

Changelog
=========
Version 0.0.0a0
---------------
**December 19, 2023**

- Initial release
- Tested the application with different Python versions ...

Credits
=======

License
=======
This program is licensed under the MIT License. For more details see the `LICENSE`_ file in the repository.

.. URLs
.. _conda: https://docs.conda.io/en/latest/
.. _pyrfc6266: https://github.com/JohnDoee/pyrfc6266
.. _venv: https://docs.python.org/3/library/venv.html
.. _Beautiful Soup: https://www.crummy.com/software/BeautifulSoup/
.. _LICENSE: ./LICENSE
.. _Requests: https://requests.readthedocs.io/en/latest/
